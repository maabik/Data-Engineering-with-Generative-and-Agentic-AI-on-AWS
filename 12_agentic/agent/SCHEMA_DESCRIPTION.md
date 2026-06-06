# Redshift Sales Data Warehouse - Schema Description

## Database Schema Overview

**Schema Name**: `sales`  
**Architecture**: Star Schema with one fact table and multiple dimension tables  
**Purpose**: Sales transaction analysis and reporting

---

## FACT TABLE

### `sales.fact_sales` (Main Transaction Table)
**Primary Key**: `sales_key` (BIGINT IDENTITY)  
**Distribution Key**: `order_date_key`  
**Sort Keys**: `order_id`, `order_date_key`

#### Core Transaction Fields:
- `sales_key` (BIGINT) - Auto-incrementing primary key
- `order_id` (VARCHAR(20)) - Unique order identifier
- `order_date_key` (INT) - Foreign key to dim_time
- `ship_date_key` (INT) - Foreign key to dim_time (nullable)

#### Foreign Keys to Dimensions:
- `account_key` (INT) - Links to dim_account
- `product_key` (INT) - Links to dim_product  
- `sales_rep_key` (INT) - Links to dim_sales_rep
- `territory_key` (INT) - Links to dim_sales_territory
- `district_key` (INT) - Links to dim_district
- `region_key` (INT) - Links to dim_region
- `zipcode_key` (INT) - Links to dim_zipcode
- `dma_key` (INT) - Links to dim_dma (nullable)

#### Measures/Metrics:
- `quantity` (INT) - Number of units sold
- `unit_price` (DECIMAL(15,2)) - Price per unit
- `unit_cost` (DECIMAL(15,2)) - Cost per unit
- `discount_pct` (DECIMAL(5,2)) - Discount percentage applied
- `sales_amount` (DECIMAL(15,2)) - Total sales revenue
- `profit_amount` (DECIMAL(15,2)) - Total profit (sales_amount - costs)

#### Flags:
- `return_flag` (BOOLEAN) - Whether item was returned
- `online_order_flag` (BOOLEAN) - Whether order was placed online

#### Audit Fields:
- `created_date` (TIMESTAMP) - Record creation timestamp
- `updated_date` (TIMESTAMP) - Last update timestamp

---

## DIMENSION TABLES

### `sales.dim_time` (Date/Time Dimension)
**Primary Key**: `time_key` (INT)  
**Distribution Key**: `time_key`  
**Sort Key**: `date_actual`

#### Date Attributes:
- `time_key` (INT) - Primary key (format: YYYYMMDD)
- `date_actual` (DATE) - Actual calendar date
- `day_of_week` (VARCHAR(10)) - Monday, Tuesday, etc.
- `day_of_month` (INT) - 1-31
- `day_of_year` (INT) - 1-365/366
- `week_of_year` (INT) - 1-53

#### Calendar Periods:
- `month_actual` (INT) - 1-12
- `month_name` (VARCHAR(10)) - January, February, etc.
- `quarter_actual` (INT) - 1-4
- `year_actual` (INT) - Calendar year

#### Fiscal Periods:
- `fiscal_month` (INT) - Fiscal month number
- `fiscal_quarter` (INT) - Fiscal quarter number
- `fiscal_year` (INT) - Fiscal year

#### Special Indicators:
- `is_holiday` (BOOLEAN) - Whether date is a holiday
- `is_weekend` (BOOLEAN) - Whether date is weekend

### `sales.dim_account` (Customer/Account Dimension)
**Primary Key**: `account_key` (INT)  
**Sort Key**: `account_id`

#### Account Identification:
- `account_key` (INT) - Primary key
- `account_id` (VARCHAR(20)) - Business account identifier
- `account_name` (VARCHAR(100)) - Company/customer name
- `account_type` (VARCHAR(50)) - Type of account

#### Business Classification:
- `industry` (VARCHAR(50)) - Industry sector
- `segment` (VARCHAR(50)) - Customer segment (Enterprise, SMB, etc.)

#### Company Details:
- `annual_revenue` (DECIMAL(15,2)) - Company's annual revenue
- `employees` (INT) - Number of employees
- `date_established` (DATE) - When company was established

#### Status & Audit:
- `active_flag` (BOOLEAN) - Whether account is active
- `created_date` (TIMESTAMP) - Record creation
- `updated_date` (TIMESTAMP) - Last update

### `sales.dim_product` (Product Dimension)
**Primary Key**: `product_key` (INT)  
**Sort Key**: `product_id`

#### Product Identification:
- `product_key` (INT) - Primary key
- `product_id` (VARCHAR(20)) - Business product identifier
- `product_name` (VARCHAR(100)) - Product name
- `product_description` (VARCHAR(500)) - Detailed description

#### Product Classification:
- `category` (VARCHAR(50)) - Main product category
- `subcategory` (VARCHAR(50)) - Product subcategory
- `brand` (VARCHAR(50)) - Product brand

#### Pricing:
- `unit_cost` (DECIMAL(15,2)) - Cost to produce/acquire
- `unit_price` (DECIMAL(15,2)) - Standard selling price

#### Status & Audit:
- `active_flag` (BOOLEAN) - Whether product is active
- `created_date` (TIMESTAMP) - Record creation
- `updated_date` (TIMESTAMP) - Last update

### `sales.dim_sales_rep` (Sales Representative Dimension)
**Primary Key**: `sales_rep_key` (INT)  
**Sort Key**: `sales_rep_id`

#### Personal Information:
- `sales_rep_key` (INT) - Primary key
- `sales_rep_id` (VARCHAR(20)) - Employee ID
- `first_name` (VARCHAR(50)) - First name
- `last_name` (VARCHAR(50)) - Last name
- `email` (VARCHAR(100)) - Email address

#### Employment Details:
- `hire_date` (DATE) - Date hired
- `territory_key` (INT) - Assigned territory
- `manager_key` (INT) - Manager's sales_rep_key (nullable)

#### Status & Audit:
- `active_flag` (BOOLEAN) - Whether employee is active
- `created_date` (TIMESTAMP) - Record creation
- `updated_date` (TIMESTAMP) - Last update

### `sales.dim_sales_territory` (Sales Territory Dimension)
**Primary Key**: `territory_key` (INT)  
**Sort Key**: `territory_id`

#### Territory Details:
- `territory_key` (INT) - Primary key
- `territory_id` (VARCHAR(20)) - Business territory identifier
- `territory_name` (VARCHAR(100)) - Territory name
- `district_key` (INT) - Parent district
- `region_key` (INT) - Parent region

#### Status & Audit:
- `active_flag` (BOOLEAN) - Whether territory is active
- `created_date` (TIMESTAMP) - Record creation
- `updated_date` (TIMESTAMP) - Last update

### `sales.dim_district` (District Dimension)
**Primary Key**: `district_key` (INT)  
**Sort Key**: `district_id`

#### District Details:
- `district_key` (INT) - Primary key
- `district_id` (VARCHAR(20)) - Business district identifier
- `district_name` (VARCHAR(100)) - District name
- `region_key` (INT) - Parent region

#### Status & Audit:
- `active_flag` (BOOLEAN) - Whether district is active
- `created_date` (TIMESTAMP) - Record creation
- `updated_date` (TIMESTAMP) - Last update

### `sales.dim_region` (Region Dimension)
**Primary Key**: `region_key` (INT)  
**Sort Key**: `region_id`

#### Region Details:
- `region_key` (INT) - Primary key
- `region_id` (VARCHAR(20)) - Business region identifier
- `region_name` (VARCHAR(100)) - Region name
- `country` (VARCHAR(50)) - Country name

#### Status & Audit:
- `active_flag` (BOOLEAN) - Whether region is active
- `created_date` (TIMESTAMP) - Record creation
- `updated_date` (TIMESTAMP) - Last update

### `sales.dim_zipcode` (Geographic Dimension)
**Primary Key**: `zipcode_key` (INT)  
**Sort Key**: `zipcode`

#### Geographic Details:
- `zipcode_key` (INT) - Primary key
- `zipcode` (VARCHAR(10)) - Postal/ZIP code
- `city` (VARCHAR(100)) - City name
- `state_province` (VARCHAR(50)) - State or province
- `country` (VARCHAR(50)) - Country name

#### Coordinates:
- `latitude` (DECIMAL(9,6)) - Geographic latitude
- `longitude` (DECIMAL(9,6)) - Geographic longitude
- `timezone` (VARCHAR(50)) - Time zone

#### Status & Audit:
- `active_flag` (BOOLEAN) - Whether zipcode is active
- `created_date` (TIMESTAMP) - Record creation
- `updated_date` (TIMESTAMP) - Last update

### `sales.dim_dma` (Designated Market Area Dimension)
**Primary Key**: `dma_key` (INT)  
**Sort Key**: `dma_id`

#### DMA Details:
- `dma_key` (INT) - Primary key
- `dma_id` (VARCHAR(20)) - DMA identifier
- `dma_name` (VARCHAR(100)) - Market area name
- `dma_rank` (INT) - Market ranking by size

#### Demographics:
- `population` (INT) - Total population
- `households` (INT) - Number of households

#### Status & Audit:
- `active_flag` (BOOLEAN) - Whether DMA is active
- `created_date` (TIMESTAMP) - Record creation
- `updated_date` (TIMESTAMP) - Last update

---

## VIEWS

### `sales.v_monthly_sales` (Monthly Sales Summary)
Pre-aggregated view showing monthly sales by geographic hierarchy:
- Time dimensions (year, month)
- Geographic hierarchy (region, district, territory)
- Aggregated sales and profit amounts

### `sales.v_product_performance` (Product Performance Summary)
Pre-aggregated view showing product performance:
- Product hierarchy (category, subcategory, product)
- Aggregated quantity, sales, profit
- Calculated profit margin percentage

---

## KEY RELATIONSHIPS

### Primary Relationships:
- `fact_sales.order_date_key` → `dim_time.time_key`
- `fact_sales.account_key` → `dim_account.account_key`
- `fact_sales.product_key` → `dim_product.product_key`
- `fact_sales.sales_rep_key` → `dim_sales_rep.sales_rep_key`

### Geographic Hierarchy:
- `fact_sales.territory_key` → `dim_sales_territory.territory_key`
- `fact_sales.district_key` → `dim_district.district_key`
- `fact_sales.region_key` → `dim_region.region_key`
- `fact_sales.zipcode_key` → `dim_zipcode.zipcode_key`
- `fact_sales.dma_key` → `dim_dma.dma_key`

### Hierarchical Relationships:
- Territory → District → Region (geographic rollup)
- Sales Rep → Territory (assignment)
- Product → Subcategory → Category (product rollup)

---

## COMMON QUERY PATTERNS

### Sales Analysis:
- Total sales by time period, region, product, or sales rep
- Profit margin analysis
- Year-over-year comparisons

### Geographic Analysis:
- Sales performance by region/district/territory
- Geographic distribution of customers

### Product Analysis:
- Top-performing products by sales/profit
- Category and brand performance

### Sales Rep Analysis:
- Individual and team performance
- Territory coverage and effectiveness

### Customer Analysis:
- Sales by industry, segment, or account size
- Customer acquisition and retention metrics
