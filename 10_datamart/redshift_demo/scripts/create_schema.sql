-- Create schema for sales data model
CREATE SCHEMA IF NOT EXISTS sales;

-- Dimension Tables

-- Time Dimension
CREATE TABLE sales.dim_time (
    time_key INT PRIMARY KEY DISTKEY,
    date_actual DATE NOT NULL,
    day_of_week VARCHAR(10) NOT NULL,
    day_of_month INT NOT NULL,
    day_of_year INT NOT NULL,
    week_of_year INT NOT NULL,
    month_actual INT NOT NULL,
    month_name VARCHAR(10) NOT NULL,
    quarter_actual INT NOT NULL,
    year_actual INT NOT NULL,
    fiscal_month INT NOT NULL,
    fiscal_quarter INT NOT NULL,
    fiscal_year INT NOT NULL,
    is_holiday BOOLEAN NOT NULL,
    is_weekend BOOLEAN NOT NULL
)
SORTKEY (date_actual);

-- Account Dimension
CREATE TABLE sales.dim_account (
    account_key INT PRIMARY KEY DISTKEY,
    account_id VARCHAR(20) NOT NULL,
    account_name VARCHAR(100) NOT NULL,
    account_type VARCHAR(50) NOT NULL,
    industry VARCHAR(50) NOT NULL,
    segment VARCHAR(50) NOT NULL,
    annual_revenue DECIMAL(15,2),
    employees INT,
    date_established DATE,
    active_flag BOOLEAN NOT NULL,
    created_date TIMESTAMP NOT NULL,
    updated_date TIMESTAMP NOT NULL
)
SORTKEY (account_id);

-- Sales Territory Dimension
CREATE TABLE sales.dim_sales_territory (
    territory_key INT PRIMARY KEY DISTKEY,
    territory_id VARCHAR(20) NOT NULL,
    territory_name VARCHAR(100) NOT NULL,
    district_key INT NOT NULL,
    region_key INT NOT NULL,
    active_flag BOOLEAN NOT NULL,
    created_date TIMESTAMP NOT NULL,
    updated_date TIMESTAMP NOT NULL
)
SORTKEY (territory_id);

-- District Dimension
CREATE TABLE sales.dim_district (
    district_key INT PRIMARY KEY DISTKEY,
    district_id VARCHAR(20) NOT NULL,
    district_name VARCHAR(100) NOT NULL,
    region_key INT NOT NULL,
    active_flag BOOLEAN NOT NULL,
    created_date TIMESTAMP NOT NULL,
    updated_date TIMESTAMP NOT NULL
)
SORTKEY (district_id);

-- Region Dimension
CREATE TABLE sales.dim_region (
    region_key INT PRIMARY KEY DISTKEY,
    region_id VARCHAR(20) NOT NULL,
    region_name VARCHAR(100) NOT NULL,
    country VARCHAR(50) NOT NULL,
    active_flag BOOLEAN NOT NULL,
    created_date TIMESTAMP NOT NULL,
    updated_date TIMESTAMP NOT NULL
)
SORTKEY (region_id);

-- Zip Code Dimension
CREATE TABLE sales.dim_zipcode (
    zipcode_key INT PRIMARY KEY DISTKEY,
    zipcode VARCHAR(10) NOT NULL,
    city VARCHAR(100) NOT NULL,
    state_province VARCHAR(50) NOT NULL,
    country VARCHAR(50) NOT NULL,
    latitude DECIMAL(9,6),
    longitude DECIMAL(9,6),
    timezone VARCHAR(50),
    active_flag BOOLEAN NOT NULL,
    created_date TIMESTAMP NOT NULL,
    updated_date TIMESTAMP NOT NULL
)
SORTKEY (zipcode);

-- DMA (Designated Market Area) Dimension
CREATE TABLE sales.dim_dma (
    dma_key INT PRIMARY KEY DISTKEY,
    dma_id VARCHAR(20) NOT NULL,
    dma_name VARCHAR(100) NOT NULL,
    dma_rank INT,
    population INT,
    households INT,
    active_flag BOOLEAN NOT NULL,
    created_date TIMESTAMP NOT NULL,
    updated_date TIMESTAMP NOT NULL
)
SORTKEY (dma_id);

-- Product Dimension
CREATE TABLE sales.dim_product (
    product_key INT PRIMARY KEY DISTKEY,
    product_id VARCHAR(20) NOT NULL,
    product_name VARCHAR(100) NOT NULL,
    product_description VARCHAR(500),
    category VARCHAR(50) NOT NULL,
    subcategory VARCHAR(50) NOT NULL,
    brand VARCHAR(50) NOT NULL,
    unit_cost DECIMAL(15,2) NOT NULL,
    unit_price DECIMAL(15,2) NOT NULL,
    active_flag BOOLEAN NOT NULL,
    created_date TIMESTAMP NOT NULL,
    updated_date TIMESTAMP NOT NULL
)
SORTKEY (product_id);

-- Sales Rep Dimension
CREATE TABLE sales.dim_sales_rep (
    sales_rep_key INT PRIMARY KEY DISTKEY,
    sales_rep_id VARCHAR(20) NOT NULL,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    email VARCHAR(100) NOT NULL,
    hire_date DATE NOT NULL,
    territory_key INT NOT NULL,
    manager_key INT,
    active_flag BOOLEAN NOT NULL,
    created_date TIMESTAMP NOT NULL,
    updated_date TIMESTAMP NOT NULL
)
SORTKEY (sales_rep_id);

-- Fact Table
CREATE TABLE sales.fact_sales (
    sales_key BIGINT IDENTITY(1,1) PRIMARY KEY,
    order_id VARCHAR(20) NOT NULL,
    order_date_key INT NOT NULL REFERENCES sales.dim_time(time_key),
    ship_date_key INT REFERENCES sales.dim_time(time_key),
    account_key INT NOT NULL REFERENCES sales.dim_account(account_key),
    product_key INT NOT NULL REFERENCES sales.dim_product(product_key),
    sales_rep_key INT NOT NULL REFERENCES sales.dim_sales_rep(sales_rep_key),
    territory_key INT NOT NULL REFERENCES sales.dim_sales_territory(territory_key),
    district_key INT NOT NULL REFERENCES sales.dim_district(district_key),
    region_key INT NOT NULL REFERENCES sales.dim_region(region_key),
    zipcode_key INT NOT NULL REFERENCES sales.dim_zipcode(zipcode_key),
    dma_key INT REFERENCES sales.dim_dma(dma_key),
    quantity INT NOT NULL,
    unit_price DECIMAL(15,2) NOT NULL,
    unit_cost DECIMAL(15,2) NOT NULL,
    discount_pct DECIMAL(5,2) NOT NULL,
    sales_amount DECIMAL(15,2) NOT NULL,
    profit_amount DECIMAL(15,2) NOT NULL,
    return_flag BOOLEAN NOT NULL DEFAULT FALSE,
    online_order_flag BOOLEAN NOT NULL,
    created_date TIMESTAMP NOT NULL,
    updated_date TIMESTAMP NOT NULL
)
DISTKEY (order_date_key)
SORTKEY (order_id, order_date_key);

-- Create some useful views
CREATE VIEW sales.v_monthly_sales AS
SELECT 
    dt.year_actual,
    dt.month_actual,
    dt.month_name,
    dr.region_name,
    dd.district_name,
    dst.territory_name,
    SUM(fs.sales_amount) as total_sales,
    SUM(fs.profit_amount) as total_profit
FROM 
    sales.fact_sales fs
    JOIN sales.dim_time dt ON fs.order_date_key = dt.time_key
    JOIN sales.dim_region dr ON fs.region_key = dr.region_key
    JOIN sales.dim_district dd ON fs.district_key = dd.district_key
    JOIN sales.dim_sales_territory dst ON fs.territory_key = dst.territory_key
GROUP BY 
    dt.year_actual,
    dt.month_actual,
    dt.month_name,
    dr.region_name,
    dd.district_name,
    dst.territory_name
ORDER BY 
    dt.year_actual,
    dt.month_actual,
    dr.region_name,
    dd.district_name,
    dst.territory_name;

CREATE VIEW sales.v_product_performance AS
SELECT 
    dp.category,
    dp.subcategory,
    dp.product_name,
    SUM(fs.quantity) as total_quantity,
    SUM(fs.sales_amount) as total_sales,
    SUM(fs.profit_amount) as total_profit,
    SUM(fs.profit_amount) / NULLIF(SUM(fs.sales_amount), 0) * 100 as profit_margin
FROM 
    sales.fact_sales fs
    JOIN sales.dim_product dp ON fs.product_key = dp.product_key
GROUP BY 
    dp.category,
    dp.subcategory,
    dp.product_name
ORDER BY 
    total_sales DESC;
