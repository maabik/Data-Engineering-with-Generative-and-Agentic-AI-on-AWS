# Sales Data Warehouse Schema for Text-to-SQL

This document provides a comprehensive description of the sales data warehouse schema, optimized for text-to-SQL conversion.

## Schema Overview

The data warehouse follows a star schema design with one central fact table (`fact_sales`) connected to multiple dimension tables. This design optimizes analytical queries for sales operations.

## Fact Table

### fact_sales
Contains all sales transaction data with foreign keys to dimension tables and measures.

| Column | Type | Description |
|--------|------|-------------|
| sale_id | INTEGER | Primary key for the sales transaction |
| date_id | INTEGER | Foreign key to dim_time table |
| product_id | INTEGER | Foreign key to dim_product table |
| account_id | INTEGER | Foreign key to dim_account table |
| sales_rep_id | INTEGER | Foreign key to dim_sales_rep table |
| territory_id | INTEGER | Foreign key to dim_sales_territory table |
| zipcode_id | INTEGER | Foreign key to dim_zipcode table |
| sales_amount | DECIMAL(18,2) | Total monetary value of the sale |
| profit_amount | DECIMAL(18,2) | Profit generated from the sale |
| quantity | INTEGER | Number of units sold |
| discount_pct | DECIMAL(5,2) | Discount percentage applied |
| is_online_order | BOOLEAN | Whether the order was placed online (TRUE) or in-store (FALSE) |
| is_returned | BOOLEAN | Whether the item was returned (TRUE) or not (FALSE) |
| transaction_date | DATE | Date when the transaction occurred |

## Dimension Tables

### dim_time
Time dimension with various date attributes for time-based analysis.

| Column | Type | Description |
|--------|------|-------------|
| date_id | INTEGER | Primary key |
| date | DATE | Calendar date |
| day_of_week | INTEGER | Day of week (1-7, where 1 is Monday) |
| day_name | VARCHAR(10) | Name of the day (Monday, Tuesday, etc.) |
| month | INTEGER | Month number (1-12) |
| month_name | VARCHAR(10) | Name of the month (January, February, etc.) |
| quarter | INTEGER | Quarter (1-4) |
| year | INTEGER | Year (YYYY) |
| is_weekend | BOOLEAN | Whether the date is a weekend (TRUE) or weekday (FALSE) |
| is_holiday | BOOLEAN | Whether the date is a holiday (TRUE) or not (FALSE) |
| fiscal_month | INTEGER | Fiscal month number |
| fiscal_quarter | INTEGER | Fiscal quarter number |
| fiscal_year | INTEGER | Fiscal year |

### dim_product
Product information including categories, pricing, and attributes.

| Column | Type | Description |
|--------|------|-------------|
| product_id | INTEGER | Primary key |
| product_name | VARCHAR(100) | Name of the product |
| product_code | VARCHAR(20) | Unique product code/SKU |
| category | VARCHAR(50) | Product category |
| subcategory | VARCHAR(50) | Product subcategory |
| brand | VARCHAR(50) | Brand name |
| base_price | DECIMAL(10,2) | Base price of the product |
| cost_price | DECIMAL(10,2) | Cost price of the product |
| weight | DECIMAL(8,2) | Weight in pounds |
| dimensions | VARCHAR(50) | Product dimensions (LxWxH) |
| color | VARCHAR(30) | Product color |
| launch_date | DATE | Date when product was launched |
| discontinue_date | DATE | Date when product was discontinued (NULL if active) |

### dim_account
Customer account information.

| Column | Type | Description |
|--------|------|-------------|
| account_id | INTEGER | Primary key |
| account_name | VARCHAR(100) | Name of the customer account |
| segment | VARCHAR(50) | Customer segment (Enterprise, SMB, Consumer) |
| industry | VARCHAR(50) | Customer's industry |
| annual_revenue | DECIMAL(15,2) | Customer's annual revenue |
| employees | INTEGER | Number of employees |
| founded_year | INTEGER | Year the customer company was founded |
| first_purchase_date | DATE | Date of first purchase |
| customer_status | VARCHAR(20) | Status (Active, Inactive, Prospect) |
| customer_lifetime_value | DECIMAL(15,2) | Calculated lifetime value of the customer |

### dim_sales_rep
Information about sales representatives.

| Column | Type | Description |
|--------|------|-------------|
| sales_rep_id | INTEGER | Primary key |
| first_name | VARCHAR(50) | First name of the sales representative |
| last_name | VARCHAR(50) | Last name of the sales representative |
| email | VARCHAR(100) | Email address |
| hire_date | DATE | Date when the sales rep was hired |
| termination_date | DATE | Date when the sales rep left (NULL if active) |
| manager_id | INTEGER | ID of the sales rep's manager (self-referencing) |
| title | VARCHAR(50) | Job title |
| department | VARCHAR(50) | Department |
| quota | DECIMAL(15,2) | Sales quota amount |
| territory_id | INTEGER | Foreign key to dim_sales_territory |

### dim_sales_territory
Sales territory hierarchy information.

| Column | Type | Description |
|--------|------|-------------|
| territory_id | INTEGER | Primary key |
| territory_name | VARCHAR(100) | Name of the sales territory |
| district_id | INTEGER | Foreign key to dim_district |
| region_id | INTEGER | Foreign key to dim_region |
| country | VARCHAR(50) | Country name |

### dim_district
District information for sales territories.

| Column | Type | Description |
|--------|------|-------------|
| district_id | INTEGER | Primary key |
| district_name | VARCHAR(100) | Name of the district |
| district_manager | VARCHAR(100) | Name of the district manager |

### dim_region
Regional information for sales territories.

| Column | Type | Description |
|--------|------|-------------|
| region_id | INTEGER | Primary key |
| region_name | VARCHAR(100) | Name of the region |
| region_manager | VARCHAR(100) | Name of the region manager |

### dim_zipcode
Geographic information for ZIP codes.

| Column | Type | Description |
|--------|------|-------------|
| zipcode_id | INTEGER | Primary key |
| zipcode | VARCHAR(10) | ZIP/Postal code |
| city | VARCHAR(100) | City name |
| state_province | VARCHAR(50) | State or province name |
| country | VARCHAR(50) | Country name |
| latitude | DECIMAL(9,6) | Latitude coordinate |
| longitude | DECIMAL(9,6) | Longitude coordinate |
| dma_id | INTEGER | Foreign key to dim_dma (Designated Market Area) |

### dim_dma
Designated Market Area information.

| Column | Type | Description |
|--------|------|-------------|
| dma_id | INTEGER | Primary key |
| dma_name | VARCHAR(100) | Name of the Designated Market Area |
| dma_code | INTEGER | DMA code |
| population | INTEGER | Population in the DMA |

## Common Query Patterns

Here are some common query patterns that can be used with this schema:

1. **Sales Analysis by Time Period**:
   - Total sales by year, quarter, month
   - Year-over-year comparison
   - Seasonal trends

2. **Product Performance**:
   - Top/bottom products by sales or profit
   - Sales by product category
   - Product return rates

3. **Customer Segmentation**:
   - Sales by customer segment
   - Industry performance
   - Customer lifetime value analysis

4. **Geographic Analysis**:
   - Sales by region, district, territory
   - Performance by country, state, city
   - DMA analysis

5. **Sales Rep Performance**:
   - Sales rep rankings
   - Performance against quota
   - New vs. tenured rep comparison

6. **Channel Analysis**:
   - Online vs. in-store sales
   - Channel performance by product category

7. **Profitability Analysis**:
   - Profit margins by product
   - Discount impact analysis
   - Cost structure analysis

## Key Relationships

- `fact_sales.date_id` → `dim_time.date_id`
- `fact_sales.product_id` → `dim_product.product_id`
- `fact_sales.account_id` → `dim_account.account_id`
- `fact_sales.sales_rep_id` → `dim_sales_rep.sales_rep_id`
- `fact_sales.territory_id` → `dim_sales_territory.territory_id`
- `fact_sales.zipcode_id` → `dim_zipcode.zipcode_id`
- `dim_sales_territory.district_id` → `dim_district.district_id`
- `dim_sales_territory.region_id` → `dim_region.region_id`
- `dim_zipcode.dma_id` → `dim_dma.dma_id`
- `dim_sales_rep.manager_id` → `dim_sales_rep.sales_rep_id` (self-referencing)
- `dim_sales_rep.territory_id` → `dim_sales_territory.territory_id`

## Example Queries

### Total Sales by Region and Year
```sql
SELECT 
    r.region_name,
    t.year,
    SUM(fs.sales_amount) AS total_sales
FROM 
    fact_sales fs
    JOIN dim_time t ON fs.date_id = t.date_id
    JOIN dim_sales_territory st ON fs.territory_id = st.territory_id
    JOIN dim_region r ON st.region_id = r.region_id
GROUP BY 
    r.region_name, t.year
ORDER BY 
    r.region_name, t.year;
```

### Top 10 Products by Sales Amount
```sql
SELECT 
    p.product_name,
    p.category,
    SUM(fs.sales_amount) AS total_sales
FROM 
    fact_sales fs
    JOIN dim_product p ON fs.product_id = p.product_id
GROUP BY 
    p.product_name, p.category
ORDER BY 
    total_sales DESC
LIMIT 10;
```

### Sales Rep Performance vs Quota
```sql
SELECT 
    sr.first_name || ' ' || sr.last_name AS sales_rep_name,
    sr.quota AS annual_quota,
    SUM(fs.sales_amount) AS actual_sales,
    (SUM(fs.sales_amount) / sr.quota) * 100 AS quota_attainment_pct
FROM 
    fact_sales fs
    JOIN dim_sales_rep sr ON fs.sales_rep_id = sr.sales_rep_id
    JOIN dim_time t ON fs.date_id = t.date_id
WHERE 
    t.year = 2023
GROUP BY 
    sr.first_name, sr.last_name, sr.quota
ORDER BY 
    quota_attainment_pct DESC;
```

### Monthly Online vs In-Store Sales
```sql
SELECT 
    t.year,
    t.month,
    t.month_name,
    SUM(CASE WHEN fs.is_online_order = TRUE THEN fs.sales_amount ELSE 0 END) AS online_sales,
    SUM(CASE WHEN fs.is_online_order = FALSE THEN fs.sales_amount ELSE 0 END) AS instore_sales
FROM 
    fact_sales fs
    JOIN dim_time t ON fs.date_id = t.date_id
GROUP BY 
    t.year, t.month, t.month_name
ORDER BY 
    t.year, t.month;
```

### Customer Segment Analysis
```sql
SELECT 
    a.segment,
    COUNT(DISTINCT a.account_id) AS num_customers,
    SUM(fs.sales_amount) AS total_sales,
    AVG(a.customer_lifetime_value) AS avg_customer_ltv
FROM 
    fact_sales fs
    JOIN dim_account a ON fs.account_id = a.account_id
GROUP BY 
    a.segment
ORDER BY 
    total_sales DESC;
```
