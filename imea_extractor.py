#!/usr/bin/env python3
"""
IMEA Direct Data Extractor
Extracts agricultural data from IMEA API using direct endpoints (no email workflow)

This extractor uses the discovered working endpoints:
1. /api/safra/seriehistoricageral - for harvest/season IDs
2. /api/seriehistorica - for historical series data with specific indicators
"""

from typing import Dict, List, Optional, Any, Union
import pandas as pd
import requests
import ssl
import logging
from datetime import datetime, timedelta
import json
import asyncio
import aiohttp
import concurrent.futures
import os
import sys
import sqlite3
import argparse
import shutil

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False
    print("⚠️  python-dotenv not installed. Install with: pip install python-dotenv")

# Try to import dateutil, fall back to manual month calculation if not available
try:
    from dateutil.relativedelta import relativedelta
    HAS_DATEUTIL = True
except ImportError:
    HAS_DATEUTIL = False

# Standalone base class for testing
class BaseExtractor:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

# /api/seriehistorica returns at most this many rows per request, whatever pageSize says
API_ROW_CAP = 10

# Activities whose series must rise monotonically to 100% within a season
CUMULATIVE_ACTIVITIES = ('Planting', 'Harvest')

# Credential management functions
def get_credentials_from_env() -> Dict[str, str]:
    """
    Get IMEA credentials from environment variables
    
    Returns:
        Dict with username and password
        
    Raises:
        ValueError: If credentials are not found in environment
    """
    username = os.getenv('IMEA_USERNAME')
    password = os.getenv('IMEA_PASSWORD')
    
    if not username or not password:
        raise ValueError(
            "❌ IMEA credentials not found in environment variables!\n"
            "Please set IMEA_USERNAME and IMEA_PASSWORD in your .env file:\n"
            "  IMEA_USERNAME=your_email@example.com\n"
            "  IMEA_PASSWORD=your_password\n"
            "Or set them as environment variables."
        )
    
    return {
        'username': username,
        'password': password
    }

# Dataset management functions
def ensure_datasets_dir(custom_dir: Optional[str] = None) -> str:
    """
    Ensure the datasets directory exists and return its path.
    
    Order of precedence:
    1. custom_dir parameter if provided
    2. IMEA_DATASETS_DIR environment variable (e.g. set in .env)
    3. ./datasets in current directory
    
    Returns:
        str: Path to the datasets directory
    """
    if custom_dir:
        os.makedirs(custom_dir, exist_ok=True)
        return custom_dir
        
    env_dir = os.getenv('IMEA_DATASETS_DIR')
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)
        return env_dir

    datasets_dir = os.path.join(os.getcwd(), 'datasets')
    os.makedirs(datasets_dir, exist_ok=True)
    return datasets_dir

def save_dataset(df: pd.DataFrame, 
                 filename: str, 
                 index: bool = False,
                 merge_on: Optional[List[str]] = None,
                 custom_dir: Optional[str] = None) -> str:
    """
    Save a DataFrame to a CSV file in the datasets directory, optionally merging with existing data.
    
    Args:
        df: DataFrame to save
        filename: Name of the CSV file
        index: Whether to include index in the CSV
        merge_on: Optional list of columns to deduplicate on when merging with existing data
        custom_dir: Optional custom directory to override default
        
    Returns:
        str: Full path to the saved file
    """
    datasets_dir = ensure_datasets_dir(custom_dir)
    file_path = os.path.join(datasets_dir, filename)

    try:
        # Merge with existing file if requested and present
        if merge_on and os.path.exists(file_path):
            try:
                existing_df = pd.read_csv(file_path)
                missing_keys = [c for c in merge_on if c not in existing_df.columns]
                if missing_keys:
                    # Old schema can't be matched on the new key: merging would collapse distinct rows
                    backup_path = f"{file_path}.{datetime.now().strftime('%Y%m%d%H%M%S')}.bak"
                    shutil.move(file_path, backup_path)
                    logging.warning(f"⚠️ {filename} lacks key columns {missing_keys}; old file moved to {backup_path}")
                    existing_df = df.iloc[0:0]
                combined_df = pd.concat([existing_df, df], ignore_index=True)
                # Normalize date keys so values read back from CSV (str) match freshly extracted ones (Timestamp)
                for col in ('date', 'publication_date'):
                    if col in combined_df.columns:
                        combined_df[col] = pd.to_datetime(combined_df[col], errors='coerce').dt.strftime('%Y-%m-%d')
                for col in merge_on:
                    if col.endswith('Id') and col in combined_df.columns:
                        combined_df[col] = combined_df[col].astype('string')
                combined_df = combined_df.drop_duplicates(subset=merge_on, keep='last')
                if 'date' in combined_df.columns:
                    combined_df = combined_df.sort_values('date').reset_index(drop=True)
                df = combined_df
            except Exception as merge_err:
                logging.warning(f"⚠️ Could not merge with existing {filename}, overwriting: {merge_err}")
                
        df.to_csv(file_path, index=index)
        logging.info(f"✅ Dataset saved: {len(df)} records → {filename}")
        return file_path
    except Exception as e:
        logging.error(f"❌ Failed to save dataset {filename}: {e}")
        raise

class TLSAdapter(requests.adapters.HTTPAdapter):
    """Custom TLS adapter for IMEA's SSL configuration with tuned connection pooling"""
    
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('pool_connections', 25)
        kwargs.setdefault('pool_maxsize', 25)
        super().__init__(*args, **kwargs)
        
    def init_poolmanager(self, *args, **kwargs):
        # Certificates are verified; the server only needs legacy renegotiation allowed
        ctx = ssl.create_default_context()
        ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
        kwargs['ssl_context'] = ctx
        return super(TLSAdapter, self).init_poolmanager(*args, **kwargs)

class IMEADirectExtractor(BaseExtractor):
    """
    IMEA Direct Data Extractor
    
    Extracts agricultural data from IMEA using direct API endpoints:
    - Historical series data (planting, harvest, commercialization) using specific indicators
    - Current price data for different crops
    """
    
    def __init__(self, credentials: Dict[str, str]):
        super().__init__()
        self.name = "IMEA Direct Extractor"
        self.base_url = 'https://api1.imea.com.br'
        self.credentials = credentials
        self.filename = 'BR_IMEA_CROP_PERCENTAGE_PROGRESS.csv'
        
        # Setup session with custom TLS adapter
        self.session = requests.Session()
        self.session.mount('https://', TLSAdapter())
        
        # IMEA data mapping with specific indicator IDs for each commodity-activity combination
        self.indicators = {
            # Cotton
            'cotton_planting': {'id': '705576963633053696', 'name': 'Cotton Planting', 'crop': 'Cotton', 'activity': 'Planting'},
            'cotton_harvest': {'id': '703492383711166464', 'name': 'Cotton Harvesting', 'crop': 'Cotton', 'activity': 'Harvest'},
            'cotton_commercialization': {'id': '703126874901708800', 'name': 'Cotton Commercialization', 'crop': 'Cotton', 'activity': 'Commercialization'},
            
            # Corn
            'corn_planting': {'id': '701211800784076800', 'name': 'Corn Planting', 'crop': 'Corn', 'activity': 'Planting'},
            'corn_harvest': {'id': '708192508847325187', 'name': 'Corn Harvesting', 'crop': 'Corn', 'activity': 'Harvest'},
            'corn_commercialization': {'id': '698758563422273536', 'name': 'Corn Commercialization', 'crop': 'Corn', 'activity': 'Commercialization'},
            
            # Soy
            'soy_planting': {'id': '708192508889268224', 'name': 'Soy Planting', 'crop': 'Soy', 'activity': 'Planting'},
            'soy_harvest': {'id': '708192508847325188', 'name': 'Soy Harvest', 'crop': 'Soy', 'activity': 'Harvest'},
            'soy_commercialization': {'id': '702389895595556864', 'name': 'Soy Commercialization', 'crop': 'Soy', 'activity': 'Commercialization'},
        }
        
        self.states = {
            '51': {'name': 'Mato Grosso', 'code': 'MT'}
        }
        
        self.access_token = None
        self.harvest_seasons = []
        
    def authenticate(self) -> bool:
        """Authenticate with IMEA API"""
        try:
            self.logger.info("🔐 Authenticating with IMEA API...")
            
            headers = {
                'content-type': 'application/x-www-form-urlencoded',
                'authorization': 'bearer undefined'
            }
            
            auth_data = {
                'username': self.credentials['username'],
                'password': self.credentials['password'],
                'grant_type': 'password',
                'client_id': '2',
            }
            
            response = self.session.post(
                f'{self.base_url}/token',
                headers=headers,
                data=auth_data,
                verify=True
            )
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data.get('access_token')
                
                if self.access_token:
                    self.logger.info("✅ Authentication successful!")
                    return True
                else:
                    self.logger.error("❌ No access token in response")
                    
            else:
                self.logger.error(f"❌ Authentication failed: {response.status_code}")
                self.logger.error(f"Response: {response.text}")
                
        except Exception as e:
            self.logger.error(f"❌ Authentication error: {e}")
            
        return False

    def fetch_harvest_seasons(self) -> List[Dict]:
        """
        Fetch all available harvest seasons using the seriehistoricageral endpoint
        
        Returns:
            List of harvest season data
        """
        if not self.access_token:
            self.logger.error("❌ No access token available")
            return []
            
        self.logger.info("📅 Fetching harvest seasons...")
        
        headers = {
            'accept': 'application/json, text/plain, */*',
            'authorization': f'bearer {self.access_token}',
            'content-type': 'application/json;charset=UTF-8',
            'origin': 'https://portal.imea.com.br',
            'referer': 'https://portal.imea.com.br/',
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
        }
        
        payload = {
            "nome": "",
            "pageSize": 100,  # Get more records
            "page": 1,
            "cadeia": [],
            "grupo": [],
            "indicador": [],
            "estado": [],
            "regiao": [],
            "cidade": []
        }
        
        try:
            response = self.session.post(
                f'{self.base_url}/api/safra/seriehistoricageral',
                headers=headers,
                json=payload,
                verify=True,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Handle both direct list and paginated response
                if isinstance(data, list):
                    seasons = data
                elif isinstance(data, dict) and 'data' in data:
                    seasons = data['data']
                else:
                    seasons = []
                
                self.harvest_seasons = seasons
                self.logger.info(f"✅ Retrieved {len(seasons)} harvest seasons")
                
                # Log season details
                for season in seasons[:10]:  # Show first 10
                    season_id = season.get('Id')
                    season_name = season.get('Nome', 'Unknown')
                    self.logger.info(f"   Season: {season_name} (ID: {season_id})")
                
                return seasons
                
            else:
                self.logger.error(f"❌ Failed to fetch harvest seasons: {response.status_code}")
                return []
                
        except Exception as e:
            self.logger.error(f"❌ Error fetching harvest seasons: {e}")
            return []

    def extract_historical_series(self, 
                                 start_date: Optional[str] = None,
                                 end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """
        Extract historical series data using specific indicator IDs with monthly granular requests
        
        Makes individual monthly requests per commodity per indicator to ensure complete data coverage
        while using tipolocalidade=1 for proper data filtering.
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            
        Returns:
            DataFrame with historical series data
        """
        if not self.access_token:
            self.logger.error("❌ No access token available")
            return None
            
        self.logger.info("🚀 Extracting historical series data with MONTHLY granular requests and tipolocalidade=1...")
        
        # Fetch harvest seasons first
        harvest_seasons = self.fetch_harvest_seasons()
        if not harvest_seasons:
            self.logger.warning("⚠️ No harvest seasons found, proceeding without season filtering")
        
        # Extract season IDs
        season_ids = [season.get('Id') for season in harvest_seasons if season.get('Id')]
        self.logger.info(f"📅 Using {len(season_ids)} harvest seasons")
        
        # Define overall date range - start from 2021 for comprehensive data
        from datetime import datetime, timedelta
        
        if start_date:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        else:
            start_dt = datetime(2021, 1, 1)  # Start from 2021 for comprehensive coverage
            
        if end_date:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        elif start_date:
            end_dt = datetime.now() + timedelta(days=15)  # Incremental: current + near future
        else:
            end_dt = datetime.now() + timedelta(days=90)  # Full: include current + next 3 months
        
        self.logger.info(f"📅 Overall date range: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}")
        
        # Generate monthly date ranges for granular requests
        def generate_monthly_ranges(start_dt, end_dt):
            """Generate monthly date ranges for individual API requests"""
            ranges = []
            current_month = start_dt.replace(day=1)  # Start at beginning of month
            
            while current_month <= end_dt:
                # Calculate end of current month
                if HAS_DATEUTIL:
                    next_month = current_month + relativedelta(months=1)
                else:
                    # Manual month calculation
                    if current_month.month == 12:
                        next_month = current_month.replace(year=current_month.year + 1, month=1)
                    else:
                        next_month = current_month.replace(month=current_month.month + 1)
                
                month_end = next_month - timedelta(days=1)
                
                # Don't exceed overall end date
                actual_end = min(month_end, end_dt)
                
                ranges.append((current_month, actual_end))
                current_month = next_month
            
            return ranges
        
        # Generate monthly date ranges for optimal granularity
        monthly_ranges = generate_monthly_ranges(start_dt, end_dt)
        self.logger.info(f"📅 Generated {len(monthly_ranges)} monthly ranges for comprehensive extraction")
        
        # Create requests for each indicator for each month
        all_requests = []
        
        for indicator_key, indicator_info in self.indicators.items():
            for range_start, range_end in monthly_ranges:
                request_info = {
                    'indicator_key': indicator_key,
                    'indicator_id': indicator_info['id'],
                    'crop': indicator_info['crop'],
                    'activity': indicator_info['activity'],
                    'start_date': range_start.strftime('%Y-%m-%d'),
                    'end_date': range_end.strftime('%Y-%m-%d'),
                    'month_label': range_start.strftime('%Y-%m'),
                    'range_label': f"{range_start.strftime('%Y-%m-%d')} to {range_end.strftime('%Y-%m-%d')}"
                }
                all_requests.append(request_info)
        
        total_requests = len(all_requests)
        months_count = len(monthly_ranges)
        self.logger.info(f"🚀 Making {total_requests} monthly requests ({len(self.indicators)} indicators × {months_count} months)")
        self.logger.info(f"⚡ Using tipolocalidade=1 for proper data filtering!")
        
        # Function to make individual requests - optimized for speed
        def make_request(request_info):
            """Make a single API request for a specific indicator and month"""
            
            payload = {
                "pageSize": 1000,  # Large page size to get all data within the month
                "inicio": request_info['start_date'],
                "fim": request_info['end_date'],
                "cadeia": [],
                "grupo": [],
                "indicador": [request_info['indicator_id']],  # Use specific indicator ID
                "tipolocalidade": ["1"],  # Always include tipolocalidade = 1
                "estado": [],  # Empty to get all states
                "regiao": [],
                "cidade": [],
                "tipoDestino": [],
                "estadoDestino": [],
                "regiaoDestino": [],
                "cidadeDestino": [],
                "safra": season_ids  # Use all available seasons
            }
            
            headers = {
                'accept': 'application/json, text/plain, */*',
                'authorization': f'bearer {self.access_token}',
                'content-type': 'application/json;charset=UTF-8',
                'origin': 'https://portal.imea.com.br',
                'referer': 'https://portal.imea.com.br/',
                'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
            }
            
            try:
                response = self.session.post(
                    f'{self.base_url}/api/seriehistorica',
                    headers=headers,
                    json=payload,
                    verify=True,
                    timeout=20  # Reasonable timeout for monthly requests
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Handle both direct list and paginated response
                    if isinstance(data, list):
                        records = data
                    elif isinstance(data, dict) and 'data' in data:
                        records = data['data']
                    else:
                        records = []
                    
                    # The API silently caps responses at 10 rows: split the window and retry each half
                    start = datetime.strptime(request_info['start_date'], '%Y-%m-%d')
                    end = datetime.strptime(request_info['end_date'], '%Y-%m-%d')
                    if len(records) >= API_ROW_CAP:
                        if start >= end:
                            self.logger.warning(f"⚠️ Hit {API_ROW_CAP}-record cap on a single day: {request_info['crop']} {request_info['activity']} {request_info['start_date']} (may be missing data)")
                        else:
                            mid = start + (end - start) / 2
                            halves = [(start, mid), (mid + timedelta(days=1), end)]
                            records = []
                            for half_start, half_end in halves:
                                sub = make_request({**request_info,
                                                    'start_date': half_start.strftime('%Y-%m-%d'),
                                                    'end_date': half_end.strftime('%Y-%m-%d')})
                                if not sub['success']:
                                    return sub
                                records.extend(sub['data'])

                    # Add metadata to each record
                    for record in records:
                        record['_crop'] = request_info['crop']
                        record['_activity'] = request_info['activity']
                        record['_indicator_key'] = request_info['indicator_key']
                        record['_month'] = request_info['month_label']
                    
                    return {
                        'success': True,
                        'data': records,
                        'crop': request_info['crop'],
                        'activity': request_info['activity'],
                        'month': request_info['month_label'],
                        'record_count': len(records)
                    }
                
                return {
                    'success': False, 
                    'error': f"HTTP {response.status_code}", 
                    'request_info': request_info
                }
                
            except Exception as e:
                return {
                    'success': False, 
                    'error': str(e), 
                    'request_info': request_info
                }
        
        # Execute requests with high parallelization
        all_data = []
        max_workers = 15  # High concurrency for speed
        
        import concurrent.futures
        
        # Process requests in batches for progress tracking
        batch_size = 50  # Good balance for monthly requests
        
        self.logger.info(f"⚡ Starting high-speed parallel extraction with {max_workers} workers...")
        
        for batch_start in range(0, total_requests, batch_size):
            batch_end = min(batch_start + batch_size, total_requests)
            batch_requests = all_requests[batch_start:batch_end]
            
            batch_num = batch_start // batch_size + 1
            total_batches = (total_requests - 1) // batch_size + 1
            
            self.logger.info(f"🔄 Processing batch {batch_num}/{total_batches} ({len(batch_requests)} monthly requests)")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_request = {executor.submit(make_request, req): req for req in batch_requests}
                
                batch_results = {'success': 0, 'failed': 0, 'total_records': 0}

                for future in concurrent.futures.as_completed(future_to_request):
                    result = future.result()

                    if result['success']:
                        data_count = result['record_count']
                        all_data.extend(result['data'])
                        batch_results['success'] += 1
                        batch_results['total_records'] += data_count

                        # Log significant results
                        if data_count > 0:
                            self.logger.info(f"✅ {result['crop']} {result['activity']} {result['month']}: {data_count} records")
                    else:
                        batch_results['failed'] += 1
                        error_info = result.get('request_info', {})
                        if batch_results['failed'] <= 5:  # Log first few failures
                            self.logger.warning(f"⚠️ Failed: {error_info.get('crop', 'Unknown')} {error_info.get('activity', 'Unknown')} {error_info.get('month_label', 'Unknown')}")
                
                self.logger.info(f"✅ Batch {batch_num} completed: {batch_results['success']} success, {batch_results['failed']} failed, {batch_results['total_records']} records")
        
        self.logger.info(f"🎯 Completed all {total_requests} monthly requests!")
        
        # Process collected data and remove duplicates
        if all_data:
            df = pd.DataFrame(all_data)
            initial_count = len(df)
            self.logger.info(f"✅ Total retrieved: {initial_count} historical records")
            
            # One observation per (date, indicator, season). Value is NOT part of the key:
            # two seasons can share a value, and one season can have a revised value.
            if 'Data' in df.columns:
                df['Data'] = pd.to_datetime(df['Data'])
                if 'SafraDescricao' not in df.columns:
                    df['SafraDescricao'] = 'Unknown'
                key = ['Data', '_indicator_key', 'SafraDescricao']
                # Record Ids are increasing snowflakes, so the highest Id is the latest revision
                df['_record_order'] = pd.to_numeric(df.get('Id'), errors='coerce')
                df = df.sort_values(key + ['_record_order'])

                conflicts = df[df.duplicated(key, keep=False)].groupby(key)['Valor'].nunique()
                conflicts = conflicts[conflicts > 1]
                for (data, ind, safra), _ in conflicts.items():
                    values = df[(df['Data'] == data) & (df['_indicator_key'] == ind) & (df['SafraDescricao'] == safra)]['Valor'].tolist()
                    self.logger.warning(f"⚠️ Conflicting values for {ind} {safra} {data.date()}: {values} → keeping latest record")

                df = df.drop_duplicates(subset=key, keep='last')
                final_count = len(df)
                self.logger.info(f"🧹 After deduplication: {initial_count} → {final_count} unique records")
            
            # Transform and clean data
            df = self._transform_historical_data(df)
            return df
        else:
            self.logger.warning("⚠️ No historical data retrieved from any requests")
            return None

    def create_percentage_summary(self, df: pd.DataFrame) -> pd.DataFrame:
        """Create a summary CSV with planted, harvested, and commercialized percentages"""
        try:
            if df.empty:
                return df
            
            # Pivot the data to have one row per date/crop/state combination.
            # Keys are unique after deduplication, so 'first' never blends two observations.
            summary = df.pivot_table(
                index=['date', 'year', 'month', 'crop', 'state', 'harvest_season'],
                columns='activity',
                values='percentage',
                aggfunc='first'
            ).reset_index()
            
            # Flatten column names
            summary.columns.name = None
            
            # Rename columns for clarity
            column_rename = {
                'Planting': 'planted_percentage',
                'Harvest': 'harvested_percentage',
                'Commercialization': 'commercialized_percentage'
            }
            summary = summary.rename(columns=column_rename)
            
            # Activities not reported on a date stay NaN: 0% would be a false observation
            for col in ['planted_percentage', 'harvested_percentage', 'commercialized_percentage']:
                if col not in summary.columns:
                    summary[col] = float('nan')
            
            # Reorder columns
            final_columns = [
                'date', 'year', 'month', 'crop', 'state', 'harvest_season',
                'planted_percentage', 'harvested_percentage', 'commercialized_percentage'
            ]
            
            summary = summary[final_columns]
            
            # Sort by date and crop
            summary = summary.sort_values(['date', 'crop']).reset_index(drop=True)
            
            self.logger.info(f"✅ Created percentage summary with {len(summary)} records")
            return summary
            
        except Exception as e:
            self.logger.error(f"❌ Error creating percentage summary: {e}")
            return df

    def extract_current_prices(self, chains: Optional[List[str]] = None) -> Optional[pd.DataFrame]:
        """
        Extract current price data using mobile endpoints
        
        Args:
            chains: List of chain IDs to extract ('1'=Cotton, '3'=Corn, '4'=Soy)
            
        Returns:
            DataFrame with current price data
        """
        self.logger.info("💰 Extracting current price data...")
        
        # Legacy chain mapping for price endpoints
        chain_mapping = {
            '1': {'name': 'Cotton', 'code': 'CTN'},
            '3': {'name': 'Corn', 'code': 'CRN'}, 
            '4': {'name': 'Soy', 'code': 'SOY'}
        }
        
        chains = chains or ['1', '3', '4']  # All crops
        all_data = []
        
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        for chain_id in chains:
            try:
                endpoint = f'/api/v2/mobile/cadeias/{chain_id}/cotacoes'
                self.logger.info(f"📊 Fetching {chain_mapping.get(chain_id, {}).get('name', f'Chain {chain_id}')} prices...")
                
                response = self.session.get(
                    f'{self.base_url}{endpoint}',
                    headers=headers,
                    verify=True
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if data:
                        # Add chain information
                        for record in data:
                            record['CadeiaId'] = chain_id
                            record['CadeiaNome'] = chain_mapping.get(chain_id, {}).get('name', f'Chain {chain_id}')
                        
                        all_data.extend(data)
                        self.logger.info(f"✅ Retrieved {len(data)} price records for chain {chain_id}")
                    else:
                        self.logger.warning(f"⚠️ No price data for chain {chain_id}")
                        
                else:
                    self.logger.error(f"❌ Price request failed for chain {chain_id}: {response.status_code}")
                    
            except Exception as e:
                self.logger.error(f"❌ Error extracting prices for chain {chain_id}: {e}")
        
        if all_data:
            df = pd.DataFrame(all_data)
            self.logger.info(f"✅ Total current price records: {len(df)}")
            
            # Transform and clean data
            df = self._transform_price_data(df)
            return df
        else:
            self.logger.warning("⚠️ No price data retrieved")
            return None
    
    def _transform_historical_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Transform and clean historical series data focusing on percentage metrics"""
        try:
            # Convert date column
            if 'Data' in df.columns:
                df['Data'] = pd.to_datetime(df['Data'])
                df['date'] = df['Data'].dt.date
                df['year'] = df['Data'].dt.year
                df['month'] = df['Data'].dt.month
            
            # Convert numeric values (these are percentages)
            if 'Valor' in df.columns:
                df['percentage'] = pd.to_numeric(df['Valor'], errors='coerce')
            
            # Use the metadata we added during extraction
            df['crop'] = df['_crop']
            df['activity'] = df['_activity']
            
            # Add state information
            if 'EstadoId' in df.columns:
                df['state'] = df['EstadoId'].map(lambda x: self.states.get(str(x), {}).get('name', f'State {x}'))
            else:
                df['state'] = 'Mato Grosso'  # Default assumption
            
            # Add harvest season info
            df['harvest_season'] = df.get('SafraDescricao', 'Unknown')
            
            # Filter only percentage data (unit should be 'Percentual')
            if 'UnidadeDescricao' in df.columns:
                df = df[df['UnidadeDescricao'] == 'Percentual'].copy()
            
            # Create final structure for CSV
            final_df = df[[
                'date', 'year', 'month', 'crop', 'activity', 'percentage', 
                'harvest_season', 'state'
            ]].copy()
            
            # Add extraction metadata
            final_df['extraction_date'] = datetime.now()
            final_df['data_source'] = 'IMEA_API_INDICATORS'
            
            self.logger.info(f"✅ Historical data transformed: {len(final_df)} percentage records")
            
            # Log breakdown by crop and activity
            breakdown = final_df.groupby(['crop', 'activity']).size().reset_index(name='count')
            self.logger.info(f"📊 Data breakdown by crop and activity:")
            for _, row in breakdown.iterrows():
                self.logger.info(f"   {row['crop']} {row['activity']}: {row['count']} records")
            
            return final_df
            
        except Exception as e:
            self.logger.error(f"❌ Error transforming historical data: {e}")
            return df
    
    def _transform_price_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Transform and clean price data"""
        try:
            # Convert numeric values
            if 'Valor' in df.columns:
                df['value'] = pd.to_numeric(df['Valor'], errors='coerce')
            
            if 'Variacao' in df.columns:
                df['variation'] = pd.to_numeric(df['Variacao'], errors='coerce')
            
            # Convert date
            if 'DataPublicacao' in df.columns:
                df['publication_date'] = pd.to_datetime(df['DataPublicacao'])
            
            # Add extraction metadata
            df['extraction_date'] = datetime.now()
            df['source'] = 'IMEA_DIRECT_PRICES'
            
            # Reorder columns
            key_columns = [
                'IndicadorFinalId', 'Localidade', 'TipoLocalidadeId', 'value', 'variation', 'CadeiaNome', 'Safra',
                'UnidadeDescricao', 'publication_date', 'extraction_date', 'source'
            ]
            key_columns = [c for c in key_columns if c in df.columns]

            # Add remaining columns
            remaining_columns = [col for col in df.columns if col not in key_columns]
            df = df[key_columns + remaining_columns]
            
            self.logger.info("✅ Price data transformed successfully")
            return df
            
        except Exception as e:
            self.logger.error(f"❌ Error transforming price data: {e}")
            return df
    
    def extract(self, **kwargs) -> Dict[str, Any]:
        """
        Main extraction method
        
        Args:
            **kwargs: Optional parameters for customization
                - extract_historical: bool (default True)
                - extract_prices: bool (default True)
                - start_date: str (YYYY-MM-DD format)
                - end_date: str (YYYY-MM-DD format)
        
        Returns:
            Dictionary with extraction results
        """
        self.logger.info(f"🚀 Starting {self.name}")
        
        # Authenticate
        if not self.authenticate():
            return {
                'success': False,
                'error': 'Authentication failed',
                'data': {}
            }
        
        results = {
            'success': True,
            'extraction_time': datetime.now(),
            'data': {}
        }
        
        # Extract historical series using specific indicators
        if kwargs.get('extract_historical', True):
            historical_df = self.extract_historical_series(
                start_date=kwargs.get('start_date'),
                end_date=kwargs.get('end_date')
            )
            
            if historical_df is not None and not historical_df.empty:
                # Create the percentage summary
                percentage_summary = self.create_percentage_summary(historical_df)
                results['data']['percentage_summary'] = percentage_summary
                results['data']['detailed_data'] = historical_df
                self.logger.info(f"📈 Percentage data: {len(percentage_summary)} summary records, {len(historical_df)} detailed records")
            else:
                results['data']['percentage_summary'] = pd.DataFrame()
                results['data']['detailed_data'] = pd.DataFrame()
                self.logger.warning("⚠️ No historical percentage data extracted")
        
        # Extract current prices
        if kwargs.get('extract_prices', True):
            prices_df = self.extract_current_prices(
                chains=['1', '3', '4']  # All crops
            )
            
            if prices_df is not None:
                results['data']['current_prices'] = prices_df
                self.logger.info(f"💰 Current prices: {len(prices_df)} records")
            else:
                results['data']['current_prices'] = pd.DataFrame()
                self.logger.warning("⚠️ No current prices data extracted")
        
        # Summary
        total_records = sum(len(df) for df in results['data'].values() if isinstance(df, pd.DataFrame))
        self.logger.info(f"🎉 Extraction completed: {total_records} total records")
        
        return results

    def save_separated_files(self, df: pd.DataFrame) -> None:
        """
        Save extracted data as separate CSV files for each crop and activity combination.
        
        Args:
            df: DataFrame to save
        """
        try:
            crops = ['Soy', 'Corn', 'Cotton']
            activities = [
                ('Planting', 'planted_percentage', 'PLANTING'),
                ('Harvest', 'harvested_percentage', 'HARVEST'), 
                ('Commercialization', 'commercialized_percentage', 'COMMERCIALIZATION')
            ]
            
            saved_files = []
            total_records = 0
            
            for crop in crops:
                # Filter data for this crop
                crop_data = df[df['crop'] == crop].copy()
                
                if crop_data.empty:
                    self.logger.warning(f"⚠️ No data for {crop}")
                    continue
                
                for activity_name, percentage_col, file_suffix in activities:
                    # Check if percentage column exists (it should in the pivoted summary)
                    if percentage_col not in crop_data.columns:
                        self.logger.warning(f"⚠️ Column {percentage_col} not found for {crop} {activity_name}")
                        continue
                    
                    # Keep every reported value for this activity, including a genuine 0%
                    activity_data = crop_data[crop_data[percentage_col].notna()].copy()
                    
                    if activity_data.empty:
                        self.logger.warning(f"⚠️ No {activity_name.lower()} data for {crop}")
                        continue
                    
                    # Create clean dataset for this crop-activity combination
                    clean_data = activity_data[[
                        'date', 'year', 'month', 'crop', 'state', 'harvest_season', percentage_col
                    ]].copy()
                    
                    # Rename the percentage column to just 'percentage' for clarity
                    clean_data = clean_data.rename(columns={percentage_col: 'percentage'})
                    
                    # Sort by date
                    clean_data = clean_data.sort_values('date').reset_index(drop=True)
                    
                    # Create filename
                    crop_upper = crop.upper()
                    filename = f'BR_IMEA_{crop_upper}_{file_suffix}_PERCENTAGE.csv'
                    
                    # Save the file with incremental deduplication
                    file_path = save_dataset(
                        clean_data, 
                        filename, 
                        index=False,
                        merge_on=['date', 'harvest_season']
                    )
                    saved_files.append((filename, len(clean_data), file_path))
                    total_records += len(clean_data)
                    
                    self.logger.info(f"📊 Saved {crop} {activity_name}: {len(clean_data)} records to {filename}")
            
            # Print summary
            self.logger.info(f"\n📋 SEPARATED FILES SUMMARY:")
            self.logger.info(f"Total files created/updated: {len(saved_files)}")
            self.logger.info(f"Total records processed: {total_records:,}")
            if not df.empty:
                self.logger.info(f"Date range: {df['date'].min()} to {df['date'].max()}")
                self.logger.info(f"Harvest seasons: {sorted(df['harvest_season'].unique())}")
            
            # List all saved files
            self.logger.info(f"\n📁 Files created/updated:")
            for filename, record_count, file_path in saved_files:
                self.logger.info(f"  {filename}: {record_count:,} records")
            
        except Exception as e:
            raise ValueError(f"Failed to save separated files: {str(e)}")
            
    def save_price_datasets(self, prices_df: pd.DataFrame) -> None:
        """
        Save price data as consolidated and per-crop CSV files.
        
        Args:
            prices_df: DataFrame with current prices
        """
        if prices_df is None or prices_df.empty:
            self.logger.warning("⚠️ No price data to save")
            return
            
        try:
            self.logger.info("💾 Saving price datasets...")
            
            # Clean and order columns. IndicadorFinalId identifies WHAT the number is
            # (spot price, operating cost, area...) and must never be dropped.
            preferred_cols = [
                'IndicadorFinalId', 'Localidade', 'TipoLocalidadeId', 'value', 'variation', 'CadeiaNome', 'Safra',
                'UnidadeDescricao', 'UnidadeSigla', 'publication_date', 'extraction_date'
            ]
            cols = [c for c in preferred_cols if c in prices_df.columns]
            clean_prices = prices_df[cols].copy()

            price_merge_keys = [c for c in ['IndicadorFinalId', 'CadeiaNome', 'Localidade', 'TipoLocalidadeId', 'Safra', 'UnidadeSigla', 'publication_date'] if c in clean_prices.columns]

            # The API itself repeats some quotes; only repeats with different values are a real ambiguity
            dup_rows = clean_prices[clean_prices.duplicated(subset=price_merge_keys, keep=False)]
            if not dup_rows.empty:
                conflicting = dup_rows.groupby(price_merge_keys, dropna=False)['value'].nunique().gt(1).sum()
                self.logger.warning(f"⚠️ {len(dup_rows)} price rows repeat a key ({conflicting} keys with differing values); keeping the last of each")
            
            # Save consolidated prices
            save_dataset(
                clean_prices, 
                'BR_IMEA_CURRENT_PRICES.csv', 
                index=False,
                merge_on=price_merge_keys if price_merge_keys else None
            )
            
            # Save per crop
            crop_map = {
                'Soy': 'BR_IMEA_SOY_PRICES.csv',
                'Corn': 'BR_IMEA_CORN_PRICES.csv',
                'Cotton': 'BR_IMEA_COTTON_PRICES.csv'
            }
            
            for crop_name, filename in crop_map.items():
                crop_subset = clean_prices[clean_prices['CadeiaNome'] == crop_name].copy()
                if not crop_subset.empty:
                    save_dataset(
                        crop_subset, 
                        filename, 
                        index=False,
                        merge_on=price_merge_keys if price_merge_keys else None
                    )
                    self.logger.info(f"📊 Saved {crop_name} prices: {len(crop_subset)} records → {filename}")
                    
        except Exception as e:
            self.logger.error(f"❌ Error saving price datasets: {e}")
            raise

    def build_coverage_report(self, filename: str = 'BR_IMEA_COVERAGE_REPORT.csv') -> pd.DataFrame:
        """
        Audit the saved progress series (not just this run) and write one row per
        crop × activity × season with its coverage and quality status.

        Status values:
            ok             - nothing suspicious
            incomplete     - cumulative series stopped below 100% and is no longer updating
            non_monotonic  - cumulative series goes down within the season (source error or revision)
            gap            - interval between observations larger than the series cadence allows
            missing        - expected season absent (e.g. harvest of a fully planted season)
        """
        datasets_dir = ensure_datasets_dir()
        series = []
        for crop in ['Soy', 'Corn', 'Cotton']:
            for activity in ['Planting', 'Harvest', 'Commercialization']:
                csv_path = os.path.join(datasets_dir, f'BR_IMEA_{crop.upper()}_{activity.upper()}_PERCENTAGE.csv')
                if os.path.exists(csv_path):
                    series_df = pd.read_csv(csv_path, parse_dates=['date'])
                    series_df['activity'] = activity
                    series.append(series_df)
        if not series:
            return pd.DataFrame()

        data = pd.concat(series, ignore_index=True)
        latest_date = data['date'].max()
        rows = []

        for (crop, activity, season), g in data.sort_values('date').groupby(['crop', 'activity', 'harvest_season']):
            cumulative = activity in CUMULATIVE_ACTIVITIES
            max_gap = int(g['date'].diff().dt.days.max()) if len(g) > 1 else 0
            drops = g[g['percentage'].diff() < -0.01]
            issues = []
            if cumulative and not drops.empty:
                issues.append('non_monotonic')
            if max_gap > (14 if cumulative else 45):
                issues.append('gap')
            if cumulative and g['percentage'].max() < 100 and (latest_date - g['date'].max()).days > 30:
                issues.append('incomplete')
            rows.append({
                'crop': crop, 'activity': activity, 'harvest_season': season,
                'first_date': g['date'].min().strftime('%Y-%m-%d'),
                'last_date': g['date'].max().strftime('%Y-%m-%d'),
                'observations': len(g),
                'max_percentage': g['percentage'].max(),
                'max_gap_days': max_gap,
                'decrease_dates': ';'.join(drops['date'].dt.strftime('%Y-%m-%d')),
                'status': ';'.join(issues) or 'ok',
            })

        # A fully planted season must eventually show harvest progress (~4-7 months later)
        planted = data[(data['activity'] == 'Planting') & (data['percentage'] >= 100)]
        harvested = data[data['activity'] == 'Harvest'].groupby('crop')['harvest_season'].unique()
        for (crop, season), g in planted.groupby(['crop', 'harvest_season']):
            done = g['date'].min()
            if season not in harvested.get(crop, []) and (latest_date - done).days > 210:
                rows.append({
                    'crop': crop, 'activity': 'Harvest', 'harvest_season': season,
                    'first_date': None, 'last_date': None, 'observations': 0,
                    'max_percentage': None, 'max_gap_days': None, 'decrease_dates': '',
                    'status': 'missing',
                })

        report = pd.DataFrame(rows).sort_values(['crop', 'activity', 'harvest_season']).reset_index(drop=True)
        report.to_csv(os.path.join(datasets_dir, filename), index=False)

        problems = report[report['status'] != 'ok']
        self.logger.info(f"🔎 Coverage report: {len(report)} series, {len(problems)} with issues → {filename}")
        for _, row in problems.iterrows():
            detail = f" (decreases on {row['decrease_dates']})" if row['decrease_dates'] else ''
            self.logger.warning(f"⚠️ {row['crop']} {row['activity']} {row['harvest_season']}: {row['status']}{detail}")
        return report

    def export_to_sqlite(self, db_filename: str = 'imea.db') -> str:
        """
        Export all datasets into an indexed SQLite database for fast analytical queries and LLM access.
        
        Args:
            db_filename: Name of the SQLite database file
            
        Returns:
            str: Full path to the SQLite database
        """
        datasets_dir = ensure_datasets_dir()
        db_path = os.path.join(datasets_dir, db_filename)
        self.logger.info(f"🗄️  Exporting datasets to SQLite: {db_path}...")
        
        conn = sqlite3.connect(db_path)
        try:
            crops = ['SOY', 'CORN', 'COTTON']
            activities = ['PLANTING', 'HARVEST', 'COMMERCIALIZATION']
            all_series = []
            
            for crop in crops:
                for activity in activities:
                    csv_path = os.path.join(datasets_dir, f'BR_IMEA_{crop}_{activity}_PERCENTAGE.csv')
                    if os.path.exists(csv_path):
                        series_df = pd.read_csv(csv_path)
                        series_df['activity'] = activity.capitalize()
                        all_series.append(series_df)
            
            if all_series:
                unified_df = pd.concat(all_series, ignore_index=True)
                unified_df.to_sql('crop_percentages', conn, if_exists='replace', index=False)
                
                # Indexes for lightning fast queries
                cursor = conn.cursor()
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_pct_crop_date ON crop_percentages (crop, date);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_pct_season ON crop_percentages (harvest_season);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_pct_activity ON crop_percentages (crop, activity);")
                
                # Generate crop_summary table
                summary_df = self.create_percentage_summary(unified_df)
                if not summary_df.empty:
                    summary_df.to_sql('crop_summary', conn, if_exists='replace', index=False)
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sum_crop_date ON crop_summary (crop, date);")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sum_season ON crop_summary (harvest_season);")
            
            # Export prices table if available
            prices_csv = os.path.join(datasets_dir, 'BR_IMEA_CURRENT_PRICES.csv')
            if os.path.exists(prices_csv):
                prices_df = pd.read_csv(prices_csv)
                prices_df.to_sql('crop_prices', conn, if_exists='replace', index=False)
                cursor = conn.cursor()
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_prices_crop ON crop_prices (CadeiaNome);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_prices_date ON crop_prices (publication_date);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_prices_loc ON crop_prices (Localidade);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_prices_indicator ON crop_prices (IndicadorFinalId);")

            coverage_csv = os.path.join(datasets_dir, 'BR_IMEA_COVERAGE_REPORT.csv')
            if os.path.exists(coverage_csv):
                pd.read_csv(coverage_csv).to_sql('coverage_report', conn, if_exists='replace', index=False)

            conn.commit()
            self.logger.info(f"✅ SQLite database successfully generated: {db_path}")
            return db_path
        except Exception as e:
            self.logger.error(f"❌ Error creating SQLite database: {e}")
            raise
        finally:
            conn.close()

    def sync_to_shared(self, db_filename: str = 'imea.db') -> None:
        """
        Copy datasets and the SQLite database to IMEA_SHARED_DIR (if set and existing)
        with world-readable permissions for cross-user and LLM agent access.
        """
        shared_dir = os.getenv('IMEA_SHARED_DIR')
        if not shared_dir or not os.path.isdir(shared_dir):
            return
            
        try:
            datasets_dir = ensure_datasets_dir()
            shared_datasets = os.path.join(shared_dir, 'datasets')
            os.makedirs(shared_datasets, exist_ok=True)
            
            # Sync CSV files
            for fname in os.listdir(datasets_dir):
                if fname.endswith('.csv'):
                    src = os.path.join(datasets_dir, fname)
                    dst = os.path.join(shared_datasets, fname)
                    try:
                        shutil.copy2(src, dst)
                        os.chmod(dst, 0o644)
                    except Exception as err:
                        self.logger.warning(f"⚠️ CSV sync failed for {fname}: {err}")

            # Sync SQLite database
            db_src = os.path.join(datasets_dir, db_filename)
            if os.path.exists(db_src):
                db_dst = os.path.join(shared_dir, db_filename)
                try:
                    shutil.copy2(db_src, db_dst)
                    os.chmod(db_dst, 0o644)
                except Exception as err:
                    self.logger.warning(f"⚠️ DB sync failed: {err}")
                # Older instructions pointed agents at datasets/imea.db; keep that path working
                db_alias = os.path.join(shared_datasets, db_filename)
                if not os.path.lexists(db_alias):
                    os.symlink(os.path.join('..', db_filename), db_alias)

            # Regenerate the agent-facing README so it never drifts from the data
            template = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs', 'SHARED_README.md')
            if os.path.exists(template):
                with open(template, encoding='utf-8') as f:
                    readme = f.read()
                readme = readme.replace('{SHARED_DIR}', shared_dir).replace(
                    '{UPDATED_AT}', datetime.now().strftime('%Y-%m-%d %H:%M'))
                readme_path = os.path.join(shared_dir, 'README.md')
                with open(readme_path, 'w', encoding='utf-8') as f:
                    f.write(readme)
                os.chmod(readme_path, 0o644)

            # Set directory permissions
            os.chmod(shared_dir, 0o755)
            os.chmod(shared_datasets, 0o755)
            self.logger.info(f"🔄 Datasets and SQLite synced to {shared_dir} (read-ready for LLMs)")
        except Exception as e:
            self.logger.warning(f"⚠️ Could not sync to {shared_dir}: {e}")
    
    def save(self, 
             data: Union[pd.DataFrame, Dict[str, Any]], 
             prices_df: Optional[pd.DataFrame] = None,
             export_sqlite: bool = True) -> None:
        """
        Save extracted data to CSV files and optionally export to SQLite database.
        
        Args:
            data: DataFrame or results dictionary containing percentage data
            prices_df: Optional DataFrame with price data
            export_sqlite: Whether to export to SQLite
        """
        if isinstance(data, dict):
            if 'percentage_summary' in data and not data['percentage_summary'].empty:
                self.save_separated_files(data['percentage_summary'])
            if 'current_prices' in data and not data['current_prices'].empty:
                self.save_price_datasets(data['current_prices'])
        elif isinstance(data, pd.DataFrame):
            self.save_separated_files(data)
            if prices_df is not None and not prices_df.empty:
                self.save_price_datasets(prices_df)

        self.build_coverage_report()

        if export_sqlite:
            self.export_to_sqlite()
            
        # Synchronize for cross-user and LLM access
        self.sync_to_shared()

def parse_cli_args(args=None):
    """Parse command line arguments for extractor execution"""
    parser = argparse.ArgumentParser(
        description="IMEA Direct Agricultural Data Extractor"
    )
    parser.add_argument(
        '--mode',
        choices=['incremental', 'full', 'prices-only'],
        default='incremental',
        help="Extraction mode: 'incremental' (recent data, default), 'full' (all historical from 2021), 'prices-only' (market quotes)"
    )
    parser.add_argument(
        '--days',
        type=int,
        default=60,
        help="Number of days back for incremental mode (default: 60)"
    )
    parser.add_argument(
        '--start-date',
        type=str,
        default=None,
        help="Custom start date (YYYY-MM-DD format)"
    )
    parser.add_argument(
        '--end-date',
        type=str,
        default=None,
        help="Custom end date (YYYY-MM-DD format)"
    )
    parser.add_argument(
        '--include-prices',
        action='store_true',
        default=True,
        help="Also extract current market prices (default: True)"
    )
    parser.add_argument(
        '--no-prices',
        dest='include_prices',
        action='store_false',
        help="Disable market price extraction"
    )
    parser.add_argument(
        '--export-sqlite',
        action='store_true',
        default=True,
        help="Export/update SQLite database for LLM and analysts (default: True)"
    )
    parser.add_argument(
        '--no-sqlite',
        dest='export_sqlite',
        action='store_false',
        help="Disable SQLite database export"
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default=None,
        help="Custom output directory for datasets"
    )
    
    parsed_args, _ = parser.parse_known_args(args)
    return parsed_args

def run(cli_args=None):
    """Entry point for the extractor following the standard pattern and CLI options"""
    try:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        args = parse_cli_args(cli_args)
        
        if args.output_dir:
            os.environ['IMEA_DATASETS_DIR'] = args.output_dir
            
        credentials = get_credentials_from_env()
        extractor = IMEADirectExtractor(credentials)
        
        # Configure extraction scope based on mode
        extract_historical = (args.mode != 'prices-only')
        extract_prices = args.include_prices or (args.mode == 'prices-only')
        
        if args.mode == 'incremental':
            start_date = args.start_date or (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
            end_date = args.end_date or (datetime.now() + timedelta(days=15)).strftime('%Y-%m-%d')
            logging.info(f"🔄 Mode: INCREMENTAL (last {args.days} days: {start_date} to {end_date})")
        elif args.mode == 'full':
            start_date = args.start_date or ''
            end_date = args.end_date or ''
            logging.info("🚀 Mode: FULL HISTORICAL EXTRACTION (2021 to present)")
        else: # prices-only
            start_date = ''
            end_date = ''
            logging.info("💰 Mode: PRICES ONLY")
            
        results = extractor.extract(
            extract_historical=extract_historical,
            extract_prices=extract_prices,
            start_date=start_date,
            end_date=end_date
        )
        
        if results['success']:
            extractor.save(
                results['data'], 
                export_sqlite=args.export_sqlite
            )
            return results['data']
        else:
            raise ValueError(f"Extraction failed: {results.get('error', 'Unknown error')}")
            
    except Exception as e:
        logging.error(f"Error running IMEA Direct extractor: {str(e)}")
        raise

def main():
    """Main execution function"""
    run()

if __name__ == "__main__":
    main() 