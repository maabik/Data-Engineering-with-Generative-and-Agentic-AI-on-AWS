#!/bin/bash
#
# Bootstrap the sales schema, dimension/fact tables, and the sales_comprehensive
# view that the QuickSight Q demo dataset queries. Runs everything via the
# Redshift Data API so it works against private clusters without psycopg2.
#
# Prereqs:
#   - 10_datamart/redshift_demo deploy.py has run far enough to:
#       * create the cluster ($REDSHIFT_CLUSTER_ID)
#       * create IAM role RedshiftSalesDemo and attach it to the cluster
#       * upload data to s3://$STAGING_BUCKET/data/{table}.csv
#   - This script is idempotent: tables are dropped+recreated so re-runs are safe.
#
# Usage:
#   STAGING_BUCKET=sales-demo-data-1779811025 ./bootstrap-redshift-data.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_cmd aws
require_var AWS_ACCOUNT_ID AWS_REGION REDSHIFT_CLUSTER_ID REDSHIFT_DATABASE \
            REDSHIFT_ADMIN_USER

STAGING_BUCKET="${STAGING_BUCKET:-}"
[[ -n "$STAGING_BUCKET" ]] || fatal "STAGING_BUCKET env var required (S3 bucket holding data/<table>.csv)."

REDSHIFT_IAM_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/RedshiftSalesDemo"

log "Bootstrapping schema + data into $REDSHIFT_CLUSTER_ID/$REDSHIFT_DATABASE"
log "  staging bucket: s3://$STAGING_BUCKET/data/"
log "  COPY iam role:  $REDSHIFT_IAM_ROLE_ARN"

# 1. Schema + dimension/fact tables (drop + recreate for idempotency).
redshift_exec_sql "CREATE SCHEMA IF NOT EXISTS sales;" "create schema" >/dev/null

# Drop dependent views first so DROP TABLE doesn't error.
redshift_exec_sql "
    DROP VIEW IF EXISTS sales.sales_comprehensive CASCADE;
    DROP VIEW IF EXISTS sales.v_monthly_sales CASCADE;
    DROP VIEW IF EXISTS sales.v_product_performance CASCADE;
" "drop dependent views" >/dev/null

for tbl in fact_sales dim_sales_rep dim_product dim_account dim_dma \
           dim_zipcode dim_sales_territory dim_district dim_region dim_time; do
    redshift_exec_sql "DROP TABLE IF EXISTS sales.${tbl} CASCADE;" \
        "drop sales.${tbl}" >/dev/null
done

redshift_exec_sql "
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
    ) SORTKEY (date_actual);
" "create dim_time" >/dev/null

redshift_exec_sql "
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
    ) SORTKEY (account_id);
" "create dim_account" >/dev/null

redshift_exec_sql "
    CREATE TABLE sales.dim_sales_territory (
        territory_key INT PRIMARY KEY DISTKEY,
        territory_id VARCHAR(20) NOT NULL,
        territory_name VARCHAR(100) NOT NULL,
        district_key INT NOT NULL,
        region_key INT NOT NULL,
        active_flag BOOLEAN NOT NULL,
        created_date TIMESTAMP NOT NULL,
        updated_date TIMESTAMP NOT NULL
    ) SORTKEY (territory_id);
" "create dim_sales_territory" >/dev/null

redshift_exec_sql "
    CREATE TABLE sales.dim_district (
        district_key INT PRIMARY KEY DISTKEY,
        district_id VARCHAR(20) NOT NULL,
        district_name VARCHAR(100) NOT NULL,
        region_key INT NOT NULL,
        active_flag BOOLEAN NOT NULL,
        created_date TIMESTAMP NOT NULL,
        updated_date TIMESTAMP NOT NULL
    ) SORTKEY (district_id);
" "create dim_district" >/dev/null

redshift_exec_sql "
    CREATE TABLE sales.dim_region (
        region_key INT PRIMARY KEY DISTKEY,
        region_id VARCHAR(20) NOT NULL,
        region_name VARCHAR(100) NOT NULL,
        country VARCHAR(50) NOT NULL,
        active_flag BOOLEAN NOT NULL,
        created_date TIMESTAMP NOT NULL,
        updated_date TIMESTAMP NOT NULL
    ) SORTKEY (region_id);
" "create dim_region" >/dev/null

redshift_exec_sql "
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
    ) SORTKEY (zipcode);
" "create dim_zipcode" >/dev/null

redshift_exec_sql "
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
    ) SORTKEY (dma_id);
" "create dim_dma" >/dev/null

redshift_exec_sql "
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
    ) SORTKEY (product_id);
" "create dim_product" >/dev/null

redshift_exec_sql "
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
    ) SORTKEY (sales_rep_id);
" "create dim_sales_rep" >/dev/null

redshift_exec_sql "
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
    ) DISTKEY (order_date_key)
      SORTKEY (order_id, order_date_key);
" "create fact_sales" >/dev/null

# 2. Load dimensions via COPY.
copy_dim() {
    local table="$1"
    redshift_exec_sql "
        COPY sales.${table}
        FROM 's3://${STAGING_BUCKET}/data/${table}.csv'
        IAM_ROLE '${REDSHIFT_IAM_ROLE_ARN}'
        FORMAT CSV
        IGNOREHEADER 1
        REGION '${AWS_REGION}';
    " "COPY sales.${table}" >/dev/null
}

for tbl in dim_time dim_region dim_district dim_sales_territory \
           dim_zipcode dim_dma dim_account dim_product; do
    copy_dim "$tbl"
done

# 3. dim_sales_rep — manager_key may be empty so stage in temp first.
redshift_exec_sql "
    DROP TABLE IF EXISTS public.temp_sales_rep;
    CREATE TABLE public.temp_sales_rep (
        sales_rep_key INT,
        sales_rep_id VARCHAR(20),
        first_name VARCHAR(50),
        last_name VARCHAR(50),
        email VARCHAR(100),
        hire_date DATE,
        territory_key INT,
        manager_key VARCHAR(20),
        active_flag BOOLEAN,
        created_date TIMESTAMP,
        updated_date TIMESTAMP
    );
" "create temp_sales_rep" >/dev/null

redshift_exec_sql "
    COPY public.temp_sales_rep
    FROM 's3://${STAGING_BUCKET}/data/dim_sales_rep.csv'
    IAM_ROLE '${REDSHIFT_IAM_ROLE_ARN}'
    FORMAT CSV
    IGNOREHEADER 1
    REGION '${AWS_REGION}'
    NULL AS ''
    ACCEPTINVCHARS AS ' '
    TRIMBLANKS;
" "COPY temp_sales_rep" >/dev/null

redshift_exec_sql "
    INSERT INTO sales.dim_sales_rep
    SELECT
        sales_rep_key, sales_rep_id, first_name, last_name, email, hire_date,
        territory_key,
        CASE WHEN manager_key = '' THEN NULL ELSE CAST(CAST(manager_key AS FLOAT) AS INT) END,
        active_flag, created_date, updated_date
    FROM public.temp_sales_rep;
" "load dim_sales_rep" >/dev/null

redshift_exec_sql "DROP TABLE public.temp_sales_rep;" "drop temp_sales_rep" >/dev/null

# 4. fact_sales — same temp-table trick; ship_date_key and dma_key may be blank.
redshift_exec_sql "
    DROP TABLE IF EXISTS public.temp_fact_sales;
    CREATE TABLE public.temp_fact_sales (
        order_id VARCHAR(20),
        order_date_key INT,
        ship_date_key VARCHAR(20),
        account_key INT,
        product_key INT,
        sales_rep_key INT,
        territory_key INT,
        district_key INT,
        region_key INT,
        zipcode_key INT,
        dma_key VARCHAR(20),
        quantity INT,
        unit_price DECIMAL(15,2),
        unit_cost DECIMAL(15,2),
        discount_pct DECIMAL(5,2),
        sales_amount DECIMAL(15,2),
        profit_amount DECIMAL(15,2),
        return_flag BOOLEAN,
        online_order_flag BOOLEAN,
        created_date TIMESTAMP,
        updated_date TIMESTAMP
    );
" "create temp_fact_sales" >/dev/null

redshift_exec_sql "
    COPY public.temp_fact_sales
    FROM 's3://${STAGING_BUCKET}/data/fact_sales.csv'
    IAM_ROLE '${REDSHIFT_IAM_ROLE_ARN}'
    FORMAT CSV
    IGNOREHEADER 1
    REGION '${AWS_REGION}'
    NULL AS ''
    ACCEPTINVCHARS AS ' '
    TRIMBLANKS;
" "COPY temp_fact_sales" >/dev/null

redshift_exec_sql "
    INSERT INTO sales.fact_sales (
        order_id, order_date_key, ship_date_key, account_key, product_key,
        sales_rep_key, territory_key, district_key, region_key, zipcode_key,
        dma_key, quantity, unit_price, unit_cost, discount_pct, sales_amount,
        profit_amount, return_flag, online_order_flag, created_date, updated_date
    )
    SELECT
        order_id, order_date_key,
        CASE WHEN ship_date_key = '' THEN NULL ELSE CAST(CAST(ship_date_key AS FLOAT) AS INT) END,
        account_key, product_key, sales_rep_key, territory_key, district_key,
        region_key, zipcode_key,
        CASE WHEN dma_key = '' THEN NULL ELSE CAST(CAST(dma_key AS FLOAT) AS INT) END,
        quantity, unit_price, unit_cost, discount_pct, sales_amount, profit_amount,
        return_flag, online_order_flag, created_date, updated_date
    FROM public.temp_fact_sales;
" "load fact_sales" >/dev/null

redshift_exec_sql "DROP TABLE public.temp_fact_sales;" "drop temp_fact_sales" >/dev/null

# 5. sales.sales_comprehensive — denormalized view shaped for the QS Q dataset.
#    Column names match 02-create-dataset.sh and 03-create-topic.sh.
redshift_exec_sql "
    CREATE OR REPLACE VIEW sales.sales_comprehensive AS
    SELECT
        fs.sales_key                                    AS sale_id,
        fs.order_id                                     AS order_id,
        dp.product_name                                 AS product_name,
        dp.category                                     AS category,
        dp.subcategory                                  AS subcategory,
        da.account_name                                 AS customer_name,
        da.account_id                                   AS customer_id,
        dr.region_name                                  AS region,
        dr.country                                      AS country,
        dsr.first_name || ' ' || dsr.last_name          AS sales_rep,
        dt.date_actual::TIMESTAMP                       AS sales_date,
        dz.city                                         AS city,
        fs.quantity                                     AS quantity,
        fs.unit_price                                   AS unit_price,
        fs.sales_amount                                 AS total_amount,
        fs.sales_amount * fs.discount_pct / 100         AS discount,
        fs.profit_amount                                AS profit
    FROM sales.fact_sales fs
        JOIN sales.dim_time dt           ON fs.order_date_key = dt.time_key
        JOIN sales.dim_product dp        ON fs.product_key    = dp.product_key
        JOIN sales.dim_account da        ON fs.account_key    = da.account_key
        JOIN sales.dim_region dr         ON fs.region_key     = dr.region_key
        JOIN sales.dim_sales_rep dsr     ON fs.sales_rep_key  = dsr.sales_rep_key
        JOIN sales.dim_zipcode dz        ON fs.zipcode_key    = dz.zipcode_key;
" "create sales.sales_comprehensive" >/dev/null

# 6. Useful views referenced by grant-quicksight-permissions.sh.
redshift_exec_sql "
    CREATE OR REPLACE VIEW sales.sales_simple AS
    SELECT sale_id, order_id, product_name, category, customer_name, region,
           sales_date, quantity, total_amount, profit
    FROM sales.sales_comprehensive;
" "create sales.sales_simple" >/dev/null

redshift_exec_sql "
    CREATE OR REPLACE VIEW sales.v_monthly_sales AS
    SELECT
        dt.year_actual, dt.month_actual, dt.month_name,
        dr.region_name, dd.district_name, dst.territory_name,
        SUM(fs.sales_amount) AS total_sales,
        SUM(fs.profit_amount) AS total_profit
    FROM sales.fact_sales fs
        JOIN sales.dim_time dt              ON fs.order_date_key = dt.time_key
        JOIN sales.dim_region dr            ON fs.region_key     = dr.region_key
        JOIN sales.dim_district dd          ON fs.district_key   = dd.district_key
        JOIN sales.dim_sales_territory dst  ON fs.territory_key  = dst.territory_key
    GROUP BY dt.year_actual, dt.month_actual, dt.month_name,
             dr.region_name, dd.district_name, dst.territory_name;
" "create sales.v_monthly_sales" >/dev/null

redshift_exec_sql "
    CREATE OR REPLACE VIEW sales.v_product_performance AS
    SELECT dp.category, dp.subcategory, dp.product_name,
           SUM(fs.quantity)      AS total_quantity,
           SUM(fs.sales_amount)  AS total_sales,
           SUM(fs.profit_amount) AS total_profit,
           SUM(fs.profit_amount) / NULLIF(SUM(fs.sales_amount), 0) * 100 AS profit_margin
    FROM sales.fact_sales fs
        JOIN sales.dim_product dp ON fs.product_key = dp.product_key
    GROUP BY dp.category, dp.subcategory, dp.product_name;
" "create sales.v_product_performance" >/dev/null

# 7. Sanity: row counts.
log "Verifying row counts..."
for tbl in dim_time dim_region dim_district dim_sales_territory dim_zipcode \
           dim_dma dim_account dim_product dim_sales_rep fact_sales \
           sales_comprehensive; do
    stmt_id=$(redshift_exec_sql \
        "SELECT COUNT(*) FROM sales.${tbl};" \
        "count sales.${tbl}")
    cnt=$(aws redshift-data get-statement-result \
        --id "$stmt_id" \
        --region "$AWS_REGION" \
        --query 'Records[0][0].longValue' \
        --output text)
    log "  sales.${tbl}: ${cnt} rows"
done

log "Bootstrap complete. Cluster ready for QuickSight Q demo."
