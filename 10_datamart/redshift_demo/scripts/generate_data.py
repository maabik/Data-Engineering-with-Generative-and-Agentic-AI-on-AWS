#!/usr/bin/env python3
import pandas as pd
import numpy as np
import random
import datetime
import os
from faker import Faker
import uuid
from dateutil.relativedelta import relativedelta
from config import config

# Initialize Faker
fake = Faker()

# Configuration from config file
OUTPUT_DIR = 'data'
START_DATE = datetime.datetime.strptime(config.get('data_generation.start_date'), '%Y-%m-%d').date()
END_DATE = datetime.datetime.strptime(config.get('data_generation.end_date'), '%Y-%m-%d').date()
NUM_ACCOUNTS = config.get('data_generation.num_accounts')
NUM_REGIONS = config.get('data_generation.num_regions')
NUM_DISTRICTS = config.get('data_generation.num_districts')
NUM_TERRITORIES = config.get('data_generation.num_territories')
NUM_ZIPCODES = config.get('data_generation.num_zipcodes')
NUM_DMAS = config.get('data_generation.num_dmas')
NUM_PRODUCTS = config.get('data_generation.num_products')
NUM_SALES_REPS = config.get('data_generation.num_sales_reps')
NUM_SALES_RECORDS = config.get('data_generation.num_sales_records')

# Create output directory if it doesn't exist
os.makedirs(OUTPUT_DIR, exist_ok=True)

def generate_time_dimension():
    """Generate time dimension data"""
    print("Generating time dimension data...")
    
    # Generate dates from start to end
    current_date = START_DATE
    time_data = []
    time_key = 10000  # Starting key
    
    while current_date <= END_DATE:
        # Calculate fiscal periods (assuming fiscal year starts in October)
        if current_date.month >= 10:
            fiscal_month = current_date.month - 9
            fiscal_year = current_date.year + 1
        else:
            fiscal_month = current_date.month + 3
            fiscal_year = current_date.year
            
        fiscal_quarter = (fiscal_month - 1) // 3 + 1
        
        # Check if it's a holiday (simplified)
        is_holiday = False
        if (current_date.month == 1 and current_date.day == 1) or \
           (current_date.month == 7 and current_date.day == 4) or \
           (current_date.month == 12 and current_date.day == 25):
            is_holiday = True
            
        # Check if it's a weekend
        is_weekend = current_date.weekday() >= 5
        
        time_data.append({
            'time_key': time_key,
            'date_actual': current_date,
            'day_of_week': current_date.strftime('%A'),
            'day_of_month': current_date.day,
            'day_of_year': int(current_date.strftime('%j')),
            'week_of_year': int(current_date.strftime('%U')),
            'month_actual': current_date.month,
            'month_name': current_date.strftime('%B'),
            'quarter_actual': (current_date.month - 1) // 3 + 1,
            'year_actual': current_date.year,
            'fiscal_month': fiscal_month,
            'fiscal_quarter': fiscal_quarter,
            'fiscal_year': fiscal_year,
            'is_holiday': is_holiday,
            'is_weekend': is_weekend
        })
        
        current_date += datetime.timedelta(days=1)
        time_key += 1
        
    # Create DataFrame and save to CSV
    df_time = pd.DataFrame(time_data)
    df_time.to_csv(f'{OUTPUT_DIR}/dim_time.csv', index=False)
    print(f"Generated {len(df_time)} time dimension records")
    return df_time

def generate_region_dimension():
    """Generate region dimension data"""
    print("Generating region dimension data...")
    
    regions = []
    for i in range(1, NUM_REGIONS + 1):
        region_name = f"{fake.country_code()}-{fake.word().capitalize()} Region"
        regions.append({
            'region_key': i,
            'region_id': f"R{i:03d}",
            'region_name': region_name,
            'country': fake.country(),
            'active_flag': random.random() > 0.05,  # 5% inactive
            'created_date': fake.date_time_between(start_date='-3y', end_date='-1y'),
            'updated_date': fake.date_time_between(start_date='-1y', end_date='now')
        })
    
    df_regions = pd.DataFrame(regions)
    df_regions.to_csv(f'{OUTPUT_DIR}/dim_region.csv', index=False)
    print(f"Generated {len(df_regions)} region records")
    return df_regions

def generate_district_dimension(df_regions):
    """Generate district dimension data"""
    print("Generating district dimension data...")
    
    districts = []
    for i in range(1, NUM_DISTRICTS + 1):
        # Assign to a random region
        region_key = random.choice(df_regions['region_key'].tolist())
        region_name = df_regions[df_regions['region_key'] == region_key]['region_name'].iloc[0]
        
        district_name = f"{region_name.split()[0]} {fake.city()} District"
        districts.append({
            'district_key': i,
            'district_id': f"D{i:03d}",
            'district_name': district_name,
            'region_key': region_key,
            'active_flag': random.random() > 0.05,  # 5% inactive
            'created_date': fake.date_time_between(start_date='-3y', end_date='-1y'),
            'updated_date': fake.date_time_between(start_date='-1y', end_date='now')
        })
    
    df_districts = pd.DataFrame(districts)
    df_districts.to_csv(f'{OUTPUT_DIR}/dim_district.csv', index=False)
    print(f"Generated {len(df_districts)} district records")
    return df_districts

def generate_territory_dimension(df_districts):
    """Generate sales territory dimension data"""
    print("Generating sales territory dimension data...")
    
    territories = []
    for i in range(1, NUM_TERRITORIES + 1):
        # Assign to a random district
        district_key = random.choice(df_districts['district_key'].tolist())
        district_row = df_districts[df_districts['district_key'] == district_key].iloc[0]
        
        territory_name = f"{district_row['district_name'].split()[0]} {fake.city()} Territory"
        territories.append({
            'territory_key': i,
            'territory_id': f"T{i:03d}",
            'territory_name': territory_name,
            'district_key': district_key,
            'region_key': district_row['region_key'],
            'active_flag': random.random() > 0.05,  # 5% inactive
            'created_date': fake.date_time_between(start_date='-3y', end_date='-1y'),
            'updated_date': fake.date_time_between(start_date='-1y', end_date='now')
        })
    
    df_territories = pd.DataFrame(territories)
    df_territories.to_csv(f'{OUTPUT_DIR}/dim_sales_territory.csv', index=False)
    print(f"Generated {len(df_territories)} territory records")
    return df_territories

def generate_zipcode_dimension():
    """Generate zipcode dimension data"""
    print("Generating zipcode dimension data...")
    
    zipcodes = []
    for i in range(1, NUM_ZIPCODES + 1):
        zipcodes.append({
            'zipcode_key': i,
            'zipcode': fake.zipcode(),
            'city': fake.city(),
            'state_province': fake.state(),
            'country': fake.country(),
            'latitude': float(fake.latitude()),
            'longitude': float(fake.longitude()),
            'timezone': random.choice(['EST', 'CST', 'MST', 'PST', 'GMT']),
            'active_flag': random.random() > 0.05,  # 5% inactive
            'created_date': fake.date_time_between(start_date='-3y', end_date='-1y'),
            'updated_date': fake.date_time_between(start_date='-1y', end_date='now')
        })
    
    df_zipcodes = pd.DataFrame(zipcodes)
    df_zipcodes.to_csv(f'{OUTPUT_DIR}/dim_zipcode.csv', index=False)
    print(f"Generated {len(df_zipcodes)} zipcode records")
    return df_zipcodes

def generate_dma_dimension():
    """Generate DMA (Designated Market Area) dimension data"""
    print("Generating DMA dimension data...")
    
    dmas = []
    for i in range(1, NUM_DMAS + 1):
        dmas.append({
            'dma_key': i,
            'dma_id': f"DMA{i:03d}",
            'dma_name': f"{fake.city()} {random.choice(['Metro', 'Area', 'Region'])}",
            'dma_rank': i,
            'population': random.randint(50000, 5000000),
            'households': random.randint(20000, 2000000),
            'active_flag': random.random() > 0.05,  # 5% inactive
            'created_date': fake.date_time_between(start_date='-3y', end_date='-1y'),
            'updated_date': fake.date_time_between(start_date='-1y', end_date='now')
        })
    
    df_dmas = pd.DataFrame(dmas)
    df_dmas.to_csv(f'{OUTPUT_DIR}/dim_dma.csv', index=False)
    print(f"Generated {len(df_dmas)} DMA records")
    return df_dmas
def generate_account_dimension():
    """Generate account dimension data"""
    print("Generating account dimension data...")
    
    industries = ['Technology', 'Healthcare', 'Finance', 'Retail', 'Manufacturing', 
                 'Education', 'Government', 'Energy', 'Transportation', 'Media']
    segments = ['Enterprise', 'Mid-Market', 'SMB', 'Startup']
    account_types = ['Direct', 'Partner', 'Reseller', 'Distributor']
    
    accounts = []
    for i in range(1, NUM_ACCOUNTS + 1):
        industry = random.choice(industries)
        segment = random.choice(segments)
        account_type = random.choice(account_types)
        
        # Generate revenue based on segment
        if segment == 'Enterprise':
            revenue = random.uniform(10000000, 1000000000)
            employees = random.randint(1000, 100000)
        elif segment == 'Mid-Market':
            revenue = random.uniform(1000000, 10000000)
            employees = random.randint(100, 1000)
        elif segment == 'SMB':
            revenue = random.uniform(100000, 1000000)
            employees = random.randint(10, 100)
        else:  # Startup
            revenue = random.uniform(10000, 100000)
            employees = random.randint(1, 10)
            
        accounts.append({
            'account_key': i,
            'account_id': f"ACC{i:06d}",
            'account_name': fake.company(),
            'account_type': account_type,
            'industry': industry,
            'segment': segment,
            'annual_revenue': revenue,
            'employees': employees,
            'date_established': fake.date_between(start_date='-30y', end_date='-1y'),
            'active_flag': random.random() > 0.05,  # 5% inactive
            'created_date': fake.date_time_between(start_date='-3y', end_date='-1y'),
            'updated_date': fake.date_time_between(start_date='-1y', end_date='now')
        })
    
    df_accounts = pd.DataFrame(accounts)
    df_accounts.to_csv(f'{OUTPUT_DIR}/dim_account.csv', index=False)
    print(f"Generated {len(df_accounts)} account records")
    return df_accounts

def generate_product_dimension():
    """Generate product dimension data"""
    print("Generating product dimension data...")
    
    categories = ['Hardware', 'Software', 'Services', 'Cloud', 'Networking']
    subcategories = {
        'Hardware': ['Servers', 'Storage', 'Desktops', 'Laptops', 'Mobile', 'Peripherals'],
        'Software': ['Operating Systems', 'Databases', 'Security', 'Analytics', 'CRM', 'ERP'],
        'Services': ['Consulting', 'Support', 'Training', 'Implementation', 'Managed Services'],
        'Cloud': ['IaaS', 'PaaS', 'SaaS', 'Serverless', 'Storage', 'Compute'],
        'Networking': ['Routers', 'Switches', 'Firewalls', 'Load Balancers', 'VPN', 'SD-WAN']
    }
    brands = ['TechCorp', 'DataSys', 'CloudWave', 'NetPro', 'SecureIT', 'SoftWorks', 'HardTech']
    
    products = []
    for i in range(1, NUM_PRODUCTS + 1):
        category = random.choice(categories)
        subcategory = random.choice(subcategories[category])
        brand = random.choice(brands)
        
        # Generate cost and price based on category
        if category == 'Hardware':
            unit_cost = random.uniform(500, 5000)
        elif category == 'Software':
            unit_cost = random.uniform(100, 2000)
        elif category == 'Services':
            unit_cost = random.uniform(1000, 10000)
        elif category == 'Cloud':
            unit_cost = random.uniform(50, 500)
        else:  # Networking
            unit_cost = random.uniform(200, 3000)
            
        # Add a markup between 20% and 60%
        markup = random.uniform(1.2, 1.6)
        unit_price = unit_cost * markup
        
        products.append({
            'product_key': i,
            'product_id': f"P{i:06d}",
            'product_name': f"{brand} {subcategory} {fake.word().capitalize()}",
            'product_description': fake.sentence(),
            'category': category,
            'subcategory': subcategory,
            'brand': brand,
            'unit_cost': unit_cost,
            'unit_price': unit_price,
            'active_flag': random.random() > 0.05,  # 5% inactive
            'created_date': fake.date_time_between(start_date='-3y', end_date='-1y'),
            'updated_date': fake.date_time_between(start_date='-1y', end_date='now')
        })
    
    df_products = pd.DataFrame(products)
    df_products.to_csv(f'{OUTPUT_DIR}/dim_product.csv', index=False)
    print(f"Generated {len(df_products)} product records")
    return df_products

def generate_sales_rep_dimension(df_territories):
    """Generate sales rep dimension data"""
    print("Generating sales rep dimension data...")
    
    sales_reps = []
    for i in range(1, NUM_SALES_REPS + 1):
        # Assign to a random territory
        territory_key = random.choice(df_territories['territory_key'].tolist())
        
        # For managers, set to None for about 20% (these will be the managers)
        if i <= NUM_SALES_REPS * 0.2:
            manager_key = None
        else:
            # Assign a random manager from the first 20%
            manager_key = random.randint(1, int(NUM_SALES_REPS * 0.2))
        
        first_name = fake.first_name()
        last_name = fake.last_name()
        
        sales_reps.append({
            'sales_rep_key': i,
            'sales_rep_id': f"SR{i:04d}",
            'first_name': first_name,
            'last_name': last_name,
            'email': f"{first_name.lower()}.{last_name.lower()}@salescompany.com",
            'hire_date': fake.date_between(start_date='-10y', end_date='now'),
            'territory_key': territory_key,
            'manager_key': manager_key,
            'active_flag': random.random() > 0.05,  # 5% inactive
            'created_date': fake.date_time_between(start_date='-3y', end_date='-1y'),
            'updated_date': fake.date_time_between(start_date='-1y', end_date='now')
        })
    
    df_sales_reps = pd.DataFrame(sales_reps)
    df_sales_reps.to_csv(f'{OUTPUT_DIR}/dim_sales_rep.csv', index=False)
    print(f"Generated {len(df_sales_reps)} sales rep records")
    return df_sales_reps
def generate_fact_sales(df_time, df_accounts, df_products, df_sales_reps, df_territories, df_districts, df_regions, df_zipcodes, df_dmas):
    """Generate fact sales data"""
    print("Generating fact sales data...")
    
    # Get all time keys for orders (exclude future dates)
    today = datetime.date.today()
    valid_time_keys = df_time[df_time['date_actual'] <= today]['time_key'].tolist()
    
    # Get all keys from dimensions
    account_keys = df_accounts['account_key'].tolist()
    product_keys = df_products['product_key'].tolist()
    sales_rep_keys = df_sales_reps['sales_rep_key'].tolist()
    territory_keys = df_territories['territory_key'].tolist()
    district_keys = df_districts['district_key'].tolist()
    region_keys = df_regions['region_key'].tolist()
    zipcode_keys = df_zipcodes['zipcode_key'].tolist()
    dma_keys = df_dmas['dma_key'].tolist()
    
    # Create a mapping from territory to district and region
    territory_to_district_region = {}
    for _, row in df_territories.iterrows():
        territory_to_district_region[row['territory_key']] = (row['district_key'], row['region_key'])
    
    # Generate sales data in batches to avoid memory issues
    batch_size = 50000
    num_batches = (NUM_SALES_RECORDS + batch_size - 1) // batch_size
    
    for batch in range(num_batches):
        print(f"Generating batch {batch+1} of {num_batches}...")
        
        start_idx = batch * batch_size
        end_idx = min((batch + 1) * batch_size, NUM_SALES_RECORDS)
        current_batch_size = end_idx - start_idx
        
        sales_data = []
        for i in range(start_idx, end_idx):
            # Generate a random order date
            order_date_key = random.choice(valid_time_keys)
            order_date = df_time[df_time['time_key'] == order_date_key]['date_actual'].iloc[0]
            
            # Generate a random ship date after the order date (or None for some)
            if random.random() < 0.95:  # 95% of orders have been shipped
                ship_date_diff = random.randint(1, 10)  # Ship 1-10 days after order
                ship_date = order_date + datetime.timedelta(days=ship_date_diff)
                if ship_date <= today:
                    ship_date_rows = df_time[df_time['date_actual'] == ship_date]
                    if not ship_date_rows.empty:
                        ship_date_key = ship_date_rows['time_key'].iloc[0]
                    else:
                        ship_date_key = None
                else:
                    ship_date_key = None
            else:
                ship_date_key = None
            
            # Select random keys from dimensions
            account_key = random.choice(account_keys)
            product_key = random.choice(product_keys)
            sales_rep_key = random.choice(sales_rep_keys)
            territory_key = random.choice(territory_keys)
            
            # Get the corresponding district and region keys
            district_key, region_key = territory_to_district_region[territory_key]
            
            zipcode_key = random.choice(zipcode_keys)
            dma_key = random.choice(dma_keys) if random.random() < 0.8 else None  # 80% have DMA
            
            # Get product price and cost
            product_row = df_products[df_products['product_key'] == product_key].iloc[0]
            unit_price = product_row['unit_price']
            unit_cost = product_row['unit_cost']
            
            # Generate quantity and discount
            quantity = random.randint(1, 20)
            discount_pct = random.choice([0, 0, 0, 0.05, 0.1, 0.15, 0.2])  # Most orders have no discount
            
            # Calculate sales and profit
            sales_amount = quantity * unit_price * (1 - discount_pct)
            profit_amount = sales_amount - (quantity * unit_cost)
            
            # Determine if online order
            online_order_flag = random.random() < 0.6  # 60% are online orders
            
            # Determine if returned
            return_flag = random.random() < 0.05  # 5% are returns
            
            sales_data.append({
                'order_id': f"ORD-{str(uuid.uuid4())[:8].upper()}",
                'order_date_key': order_date_key,
                'ship_date_key': ship_date_key,
                'account_key': account_key,
                'product_key': product_key,
                'sales_rep_key': sales_rep_key,
                'territory_key': territory_key,
                'district_key': district_key,
                'region_key': region_key,
                'zipcode_key': zipcode_key,
                'dma_key': dma_key,
                'quantity': quantity,
                'unit_price': unit_price,
                'unit_cost': unit_cost,
                'discount_pct': discount_pct,
                'sales_amount': sales_amount,
                'profit_amount': profit_amount,
                'return_flag': return_flag,
                'online_order_flag': online_order_flag,
                'created_date': datetime.datetime.now(),
                'updated_date': datetime.datetime.now()
            })
        
        # Create DataFrame and save to CSV
        df_sales_batch = pd.DataFrame(sales_data)
        
        # For the first batch, write with header, for subsequent batches append without header
        if batch == 0:
            df_sales_batch.to_csv(f'{OUTPUT_DIR}/fact_sales.csv', index=False)
        else:
            df_sales_batch.to_csv(f'{OUTPUT_DIR}/fact_sales.csv', mode='a', header=False, index=False)
        
        print(f"Generated {len(df_sales_batch)} sales records in batch {batch+1}")
    
    print(f"Generated {NUM_SALES_RECORDS} total sales records")

def main():
    """Main function to generate all data"""
    print("Starting data generation for Redshift sales demo...")
    
    # Generate dimension tables
    df_time = generate_time_dimension()
    df_regions = generate_region_dimension()
    df_districts = generate_district_dimension(df_regions)
    df_territories = generate_territory_dimension(df_districts)
    df_zipcodes = generate_zipcode_dimension()
    df_dmas = generate_dma_dimension()
    df_accounts = generate_account_dimension()
    df_products = generate_product_dimension()
    df_sales_reps = generate_sales_rep_dimension(df_territories)
    
    # Generate fact table
    generate_fact_sales(df_time, df_accounts, df_products, df_sales_reps, 
                       df_territories, df_districts, df_regions, df_zipcodes, df_dmas)
    
    print("Data generation complete!")

if __name__ == "__main__":
    main()
