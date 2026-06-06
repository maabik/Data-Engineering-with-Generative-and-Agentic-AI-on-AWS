-- Sample Analytical Queries for Redshift Sales Demo
-- Replace 'sales' with your schema name if different

-- Query 1: Monthly sales and profit by region
SELECT 
    dt.year_actual,
    dt.month_actual,
    dt.month_name,
    dr.region_name,
    SUM(fs.sales_amount) as total_sales,
    SUM(fs.profit_amount) as total_profit,
    SUM(fs.profit_amount) / NULLIF(SUM(fs.sales_amount), 0) * 100 as profit_margin_pct
FROM 
    sales.fact_sales fs
    JOIN sales.dim_time dt ON fs.order_date_key = dt.time_key
    JOIN sales.dim_region dr ON fs.region_key = dr.region_key
WHERE
    fs.return_flag = FALSE
GROUP BY 
    dt.year_actual,
    dt.month_actual,
    dt.month_name,
    dr.region_name
ORDER BY 
    dt.year_actual,
    dt.month_actual,
    total_sales DESC;

-- Query 2: Top 10 products by sales amount
SELECT 
    dp.product_name,
    dp.category,
    dp.subcategory,
    dp.brand,
    SUM(fs.quantity) as total_quantity,
    SUM(fs.sales_amount) as total_sales,
    SUM(fs.profit_amount) as total_profit,
    SUM(fs.profit_amount) / NULLIF(SUM(fs.sales_amount), 0) * 100 as profit_margin_pct
FROM 
    sales.fact_sales fs
    JOIN sales.dim_product dp ON fs.product_key = dp.product_key
WHERE
    fs.return_flag = FALSE
GROUP BY 
    dp.product_name,
    dp.category,
    dp.subcategory,
    dp.brand
ORDER BY 
    total_sales DESC
LIMIT 10;

-- Query 3: Sales performance by sales rep with territory information
SELECT 
    CONCAT(dsr.first_name, ' ', dsr.last_name) as sales_rep_name,
    dst.territory_name,
    dd.district_name,
    dr.region_name,
    COUNT(DISTINCT fs.order_id) as num_orders,
    SUM(fs.sales_amount) as total_sales,
    SUM(fs.profit_amount) as total_profit,
    AVG(fs.sales_amount) as avg_order_value
FROM 
    sales.fact_sales fs
    JOIN sales.dim_sales_rep dsr ON fs.sales_rep_key = dsr.sales_rep_key
    JOIN sales.dim_sales_territory dst ON fs.territory_key = dst.territory_key
    JOIN sales.dim_district dd ON fs.district_key = dd.district_key
    JOIN sales.dim_region dr ON fs.region_key = dr.region_key
WHERE
    fs.return_flag = FALSE
GROUP BY 
    sales_rep_name,
    dst.territory_name,
    dd.district_name,
    dr.region_name
ORDER BY 
    total_sales DESC;

-- Query 4: Sales by account segment and industry
SELECT 
    da.segment,
    da.industry,
    COUNT(DISTINCT da.account_key) as num_accounts,
    COUNT(DISTINCT fs.order_id) as num_orders,
    SUM(fs.sales_amount) as total_sales,
    AVG(fs.sales_amount) as avg_order_value,
    SUM(fs.sales_amount) / COUNT(DISTINCT da.account_key) as sales_per_account
FROM 
    sales.fact_sales fs
    JOIN sales.dim_account da ON fs.account_key = da.account_key
WHERE
    fs.return_flag = FALSE
GROUP BY 
    da.segment,
    da.industry
ORDER BY 
    total_sales DESC;

-- Query 5: Year-over-year sales comparison by quarter
WITH quarterly_sales AS (
    SELECT 
        dt.year_actual,
        dt.quarter_actual,
        SUM(fs.sales_amount) as quarterly_sales,
        SUM(fs.profit_amount) as quarterly_profit
    FROM 
        sales.fact_sales fs
        JOIN sales.dim_time dt ON fs.order_date_key = dt.time_key
    WHERE
        fs.return_flag = FALSE
    GROUP BY 
        dt.year_actual,
        dt.quarter_actual
)
SELECT 
    current_year.year_actual,
    current_year.quarter_actual,
    current_year.quarterly_sales as current_year_sales,
    prev_year.quarterly_sales as previous_year_sales,
    (current_year.quarterly_sales - prev_year.quarterly_sales) as sales_difference,
    CASE 
        WHEN prev_year.quarterly_sales = 0 THEN NULL
        ELSE (current_year.quarterly_sales - prev_year.quarterly_sales) / prev_year.quarterly_sales * 100 
    END as growth_percentage
FROM 
    quarterly_sales current_year
    LEFT JOIN quarterly_sales prev_year ON 
        current_year.year_actual = prev_year.year_actual + 1 AND
        current_year.quarter_actual = prev_year.quarter_actual
WHERE 
    prev_year.quarterly_sales IS NOT NULL
ORDER BY 
    current_year.year_actual,
    current_year.quarter_actual;

-- Query 6: Online vs. In-store sales analysis
SELECT 
    dt.year_actual,
    dt.quarter_actual,
    CASE WHEN fs.online_order_flag THEN 'Online' ELSE 'In-store' END as sales_channel,
    COUNT(DISTINCT fs.order_id) as num_orders,
    SUM(fs.sales_amount) as total_sales,
    AVG(fs.sales_amount) as avg_order_value,
    SUM(fs.quantity) as total_quantity
FROM 
    sales.fact_sales fs
    JOIN sales.dim_time dt ON fs.order_date_key = dt.time_key
WHERE
    fs.return_flag = FALSE
GROUP BY 
    dt.year_actual,
    dt.quarter_actual,
    sales_channel
ORDER BY 
    dt.year_actual,
    dt.quarter_actual,
    sales_channel;

-- Query 7: Return rate analysis by product category
SELECT 
    dp.category,
    dp.subcategory,
    COUNT(DISTINCT CASE WHEN fs.return_flag THEN fs.order_id END) as returned_orders,
    COUNT(DISTINCT fs.order_id) as total_orders,
    COUNT(DISTINCT CASE WHEN fs.return_flag THEN fs.order_id END)::FLOAT / 
        NULLIF(COUNT(DISTINCT fs.order_id), 0) * 100 as return_rate_pct,
    SUM(CASE WHEN fs.return_flag THEN fs.sales_amount ELSE 0 END) as returned_sales_amount,
    SUM(fs.sales_amount) as total_sales_amount
FROM 
    sales.fact_sales fs
    JOIN sales.dim_product dp ON fs.product_key = dp.product_key
GROUP BY 
    dp.category,
    dp.subcategory
ORDER BY 
    return_rate_pct DESC;

-- Query 8: Sales performance by day of week
SELECT 
    dt.day_of_week,
    COUNT(DISTINCT fs.order_id) as num_orders,
    SUM(fs.sales_amount) as total_sales,
    AVG(fs.sales_amount) as avg_order_value,
    SUM(fs.quantity) as total_quantity
FROM 
    sales.fact_sales fs
    JOIN sales.dim_time dt ON fs.order_date_key = dt.time_key
WHERE
    fs.return_flag = FALSE
GROUP BY 
    dt.day_of_week
ORDER BY 
    CASE
        WHEN dt.day_of_week = 'Monday' THEN 1
        WHEN dt.day_of_week = 'Tuesday' THEN 2
        WHEN dt.day_of_week = 'Wednesday' THEN 3
        WHEN dt.day_of_week = 'Thursday' THEN 4
        WHEN dt.day_of_week = 'Friday' THEN 5
        WHEN dt.day_of_week = 'Saturday' THEN 6
        WHEN dt.day_of_week = 'Sunday' THEN 7
    END;

-- Query 9: Geographic sales distribution
SELECT 
    dr.region_name,
    dd.district_name,
    dst.territory_name,
    dz.state_province,
    COUNT(DISTINCT fs.order_id) as num_orders,
    SUM(fs.sales_amount) as total_sales,
    COUNT(DISTINCT fs.account_key) as num_customers
FROM 
    sales.fact_sales fs
    JOIN sales.dim_region dr ON fs.region_key = dr.region_key
    JOIN sales.dim_district dd ON fs.district_key = dd.district_key
    JOIN sales.dim_sales_territory dst ON fs.territory_key = dst.territory_key
    JOIN sales.dim_zipcode dz ON fs.zipcode_key = dz.zipcode_key
WHERE
    fs.return_flag = FALSE
GROUP BY 
    dr.region_name,
    dd.district_name,
    dst.territory_name,
    dz.state_province
ORDER BY 
    total_sales DESC;

-- Query 10: Customer lifetime value analysis
SELECT 
    da.segment,
    da.industry,
    COUNT(DISTINCT da.account_key) as num_customers,
    SUM(fs.sales_amount) as total_sales,
    SUM(fs.profit_amount) as total_profit,
    SUM(fs.sales_amount) / COUNT(DISTINCT da.account_key) as avg_customer_value,
    COUNT(DISTINCT fs.order_id) / COUNT(DISTINCT da.account_key) as avg_orders_per_customer
FROM 
    sales.fact_sales fs
    JOIN sales.dim_account da ON fs.account_key = da.account_key
WHERE
    fs.return_flag = FALSE
GROUP BY 
    da.segment,
    da.industry
ORDER BY 
    avg_customer_value DESC;
