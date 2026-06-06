# Data Pipeline Standards and Best Practices

This steering file defines standards for building data pipelines with PySpark, Apache Iceberg, and AWS Glue. Follow these guidelines when implementing medallion architecture pipelines or similar data processing workflows.

---

## Column Name Normalization

### Standard Rules

When transforming column names from source systems, apply these normalization rules consistently:

1. **Convert to lowercase**: All column names should be lowercase
2. **Replace spaces with underscores**: `"First Name"` → `"first_name"`
3. **Replace hyphens with underscores**: `"order-id"` → `"order_id"`
4. **Remove special characters**: Keep only alphanumeric and underscores
5. **Avoid reserved keywords**: Prefix with underscore if needed (e.g., `"select"` → `"_select"`)

### Implementation Pattern

```python
def normalize_column_name(col_name: str) -> str:
    """
    Normalize column names to follow data lake standards.
    
    Args:
        col_name: Original column name from source
        
    Returns:
        Normalized column name (lowercase, underscores only)
    """
    import re
    
    # Convert to lowercase
    normalized = col_name.lower()
    
    # Replace spaces and hyphens with underscores
    normalized = normalized.replace(" ", "_").replace("-", "_")
    
    # Remove special characters (keep alphanumeric and underscores)
    normalized = re.sub(r'[^a-z0-9_]', '', normalized)
    
    # Remove consecutive underscores
    normalized = re.sub(r'_+', '_', normalized)
    
    # Remove leading/trailing underscores
    normalized = normalized.strip('_')
    
    return normalized

# Apply to DataFrame
for col_name in df.columns:
    normalized_name = normalize_column_name(col_name)
    df = df.withColumnRenamed(col_name, normalized_name)
```

### Why This Matters

- **SQL compatibility**: Avoids quoting issues in queries
- **Consistency**: Predictable naming across all tables
- **Tooling support**: Works with all analytics tools
- **Case sensitivity**: Prevents case-related bugs

---

## Numeric and Decimal Precision

### Standard Rules

Choose the appropriate numeric type based on precision requirements:

#### Numeric Type Options

| Type | Use Case | Precision | Storage |
|------|----------|-----------|---------|
| `IntegerType` | Whole numbers (-2B to 2B) | Exact | 4 bytes |
| `LongType` | Large whole numbers | Exact | 8 bytes |
| `FloatType` | Approximate decimals | ~7 digits | 4 bytes |
| `DoubleType` | Approximate decimals | ~15 digits | 8 bytes |
| `DecimalType(p, s)` | Exact decimals | User-defined | Variable |

#### Use DecimalType When:
- **Financial data**: Money, prices, costs (exact precision required)
- **Measurements**: Scientific data requiring exact decimal representation
- **Regulatory compliance**: Tax calculations, accounting
- **Avoiding rounding errors**: When precision matters more than performance

#### Use DoubleType When:
- **Approximate values**: Latitude/longitude, sensor readings
- **Performance critical**: Large-scale analytics where slight imprecision is acceptable
- **Statistical calculations**: Averages, percentiles, aggregations

### DecimalType Syntax

```python
from pyspark.sql.types import DecimalType

# DecimalType(precision, scale)
# precision: total number of digits
# scale: number of digits after decimal point

DecimalType(10, 2)  # Up to 10 digits total, 2 after decimal
                     # Example: 12345678.90

DecimalType(18, 4)  # Up to 18 digits total, 4 after decimal
                     # Example: 12345678901234.5678

DecimalType(38, 10) # Maximum precision in Spark
                     # Example: 1234567890123456789012345678.1234567890
```

### Common Decimal Patterns

#### Financial Data (Currency)
```python
from pyspark.sql.functions import col
from pyspark.sql.types import DecimalType

# US Dollar amounts (up to $999,999.99)
df = df.withColumn("price", col("price").cast(DecimalType(8, 2)))
df = df.withColumn("tax_amount", col("tax_amount").cast(DecimalType(8, 2)))

# Large financial amounts (up to $999,999,999,999.99)
df = df.withColumn("revenue", col("revenue").cast(DecimalType(14, 2)))

# High-precision financial (4 decimal places for forex, crypto)
df = df.withColumn("exchange_rate", col("exchange_rate").cast(DecimalType(18, 4)))
```

#### Percentages and Rates
```python
# Percentage with 2 decimal places (0.00% to 100.00%)
df = df.withColumn("interest_rate", col("interest_rate").cast(DecimalType(5, 2)))

# Basis points (4 decimal places: 0.0000 to 100.0000)
df = df.withColumn("fee_rate", col("fee_rate").cast(DecimalType(6, 4)))
```

#### Quantities and Measurements
```python
# Product quantities (up to 999,999.999)
df = df.withColumn("quantity", col("quantity").cast(DecimalType(9, 3)))

# Weight in kilograms (up to 99,999.99 kg)
df = df.withColumn("weight_kg", col("weight_kg").cast(DecimalType(7, 2)))
```

### Choosing Precision and Scale

Use this formula to determine your DecimalType parameters:

```
precision = (digits before decimal) + (digits after decimal)
scale = (digits after decimal)
```

**Examples:**
- `$1,234.56` → `DecimalType(6, 2)` (4 digits before + 2 after)
- `99.9999%` → `DecimalType(6, 4)` (2 digits before + 4 after)
- `0.00000001` → `DecimalType(8, 8)` (0 digits before + 8 after)

### Implementation Pattern

```python
from pyspark.sql.functions import col
from pyspark.sql.types import DecimalType, DoubleType, IntegerType

# Define precision requirements
CURRENCY_PRECISION = DecimalType(14, 2)  # Standard currency
RATE_PRECISION = DecimalType(10, 4)      # Interest rates, percentages
QUANTITY_PRECISION = DecimalType(12, 3)  # Product quantities

# Apply to DataFrame
df = df.withColumn("order_amount", col("order_amount").cast(CURRENCY_PRECISION))
df = df.withColumn("discount_rate", col("discount_rate").cast(RATE_PRECISION))
df = df.withColumn("quantity", col("quantity").cast(QUANTITY_PRECISION))

# For approximate values, use DoubleType
df = df.withColumn("latitude", col("latitude").cast(DoubleType()))
df = df.withColumn("longitude", col("longitude").cast(DoubleType()))

# For whole numbers, use IntegerType or LongType
df = df.withColumn("customer_id", col("customer_id").cast(IntegerType()))
df = df.withColumn("transaction_id", col("transaction_id").cast(LongType()))
```

### Precision Limits

Spark's DecimalType has these limits:
- **Maximum precision**: 38 digits
- **Maximum scale**: 38 digits (but scale ≤ precision)

```python
# Maximum precision example
DecimalType(38, 10)  # ✅ Valid: 28 digits before, 10 after
DecimalType(38, 38)  # ✅ Valid: 0 digits before, 38 after (0.xxx...)
DecimalType(40, 2)   # ❌ Invalid: Exceeds max precision of 38
```

### Handling Overflow

When casting to DecimalType, values that exceed precision will become null:

```python
# If you cast 123456789.99 to DecimalType(8, 2)
# Result: null (exceeds 8 total digits)

# Check for nulls after casting
df = df.withColumn("amount", col("amount").cast(DecimalType(10, 2)))
null_count = df.filter(col("amount").isNull()).count()
if null_count > 0:
    print(f"Warning: {null_count} values exceeded precision and became null")
```

### Performance Considerations

**DecimalType vs DoubleType:**
- DecimalType: Slower, exact precision, larger storage
- DoubleType: Faster, approximate, smaller storage

**Rule of thumb:**
- Financial/regulatory data → DecimalType
- Analytics/ML features → DoubleType
- Counts/IDs → IntegerType/LongType

### Complete Example

```python
from pyspark.sql.functions import col
from pyspark.sql.types import DecimalType, DoubleType, IntegerType, LongType

# Define precision constants
MONEY = DecimalType(14, 2)
RATE = DecimalType(10, 4)
QUANTITY = DecimalType(12, 3)

# Apply type casting with appropriate precision
df = df \
    .withColumn("order_id", col("order_id").cast(LongType())) \
    .withColumn("customer_id", col("customer_id").cast(IntegerType())) \
    .withColumn("order_amount", col("order_amount").cast(MONEY)) \
    .withColumn("tax_amount", col("tax_amount").cast(MONEY)) \
    .withColumn("discount_rate", col("discount_rate").cast(RATE)) \
    .withColumn("quantity", col("quantity").cast(QUANTITY)) \
    .withColumn("latitude", col("latitude").cast(DoubleType())) \
    .withColumn("longitude", col("longitude").cast(DoubleType()))

# Validate no precision overflow
for col_name in ["order_amount", "tax_amount", "discount_rate", "quantity"]:
    null_count = df.filter(col(col_name).isNull()).count()
    if null_count > 0:
        print(f"⚠️  Warning: {col_name} has {null_count} null values after casting")
```

---

## Date and Timestamp Field Handling

### Standard Rules

Choose the appropriate temporal type based on your use case:

#### Use DateType When:
- You only need date precision (year, month, day)
- Time of day is not relevant to your analysis
- You want to partition by date
- Examples: birth_date, order_date, event_date

#### Use TimestampType When:
- You need time-of-day precision
- Tracking exact moments in time
- Audit trails and event logging
- Examples: created_at, updated_at, transaction_timestamp

### Default Recommendation

**For most business data, prefer DateType over TimestampType** because:
- Smaller storage footprint
- Simpler queries (no timezone complexity)
- Better partition pruning performance
- Clearer business semantics

### Implementation Pattern

```python
from pyspark.sql.functions import col, to_date, to_timestamp
from pyspark.sql.types import DateType, TimestampType

# Cast to DateType (recommended for business dates)
df = df.withColumn("order_date", to_date(col("order_date")))
df = df.withColumn("birth_date", to_date(col("birth_date")))

# Cast to TimestampType (for precise event times)
df = df.withColumn("created_at", to_timestamp(col("created_at")))
df = df.withColumn("last_modified_at", to_timestamp(col("last_modified_at")))

# Alternative: Direct casting
df = df.withColumn("order_date", col("order_date").cast(DateType()))
df = df.withColumn("created_at", col("created_at").cast(TimestampType()))
```

### Date Field Naming Convention

Use suffixes to indicate the temporal type:
- `*_date` for DateType fields: `order_date`, `ship_date`
- `*_at` or `*_timestamp` for TimestampType: `created_at`, `updated_at`, `event_timestamp`

---

## Partition Column Selection

### Decision Framework

Choosing the right partition column(s) is critical for query performance. Follow this decision tree:

#### 1. Identify Query Patterns

Ask these questions:
- What filters appear in 80% of queries?
- What's the most common WHERE clause?
- Are queries time-based, geography-based, or entity-based?

#### 2. Evaluate Cardinality

**Good partition cardinality**: 100 to 10,000 partitions
- Too few partitions (< 100): Limited parallelism, large files
- Too many partitions (> 10,000): Metadata overhead, small files

#### 3. Partition Column Priority

**Priority 1: Date/Time Fields**
- Most common query filter
- Natural data lifecycle (retention, archival)
- Enables time-travel and incremental processing

**Priority 2: Geographic/Organizational Fields**
- Region, country, state
- Business unit, department
- Customer segment

**Priority 3: High-Cardinality IDs**
- Only if frequently filtered
- Use hash partitioning or bucketing instead

### Date-Based Partitioning Strategies

#### Strategy 1: Daily Partitioning
```python
# Best for: High-volume data, daily processing
.partitionedBy("order_date")

# Query example
SELECT * FROM orders WHERE order_date = '2024-01-15'
```

**Use when:**
- Data arrives daily
- Queries typically filter by specific dates
- Data volume: > 1GB per day

#### Strategy 2: Monthly Partitioning (Iceberg Transform)
```python
# Best for: Medium-volume data, monthly reporting
.partitionedBy("months(order_date)")

# Iceberg automatically handles the transform
SELECT * FROM orders WHERE order_date BETWEEN '2024-01-01' AND '2024-01-31'
```

**Use when:**
- Data volume: 100MB - 1GB per day
- Queries span date ranges
- Monthly aggregations are common

#### Strategy 3: Yearly Partitioning
```python
# Best for: Low-volume historical data
.partitionedBy("years(order_date)")
```

**Use when:**
- Data volume: < 100MB per day
- Long-term historical analysis
- Infrequent updates

#### Strategy 4: Multi-Dimensional Partitioning
```python
# Best for: Complex query patterns
.partitionedBy("order_date", "region")

# Or with transforms
.partitionedBy("months(order_date)", "region")
```

**Use when:**
- Multiple common filter dimensions
- Cardinality product is reasonable (e.g., 12 months × 50 regions = 600 partitions)

### Partition Column Selection Checklist

Before choosing partition columns, verify:

- [ ] Column appears in > 50% of queries
- [ ] Cardinality is between 100 and 10,000
- [ ] Column values are relatively stable (not frequently updated)
- [ ] Column is not nullable (or nulls are rare)
- [ ] Partitions will have roughly balanced data distribution
- [ ] Column has business meaning (not just technical IDs)

### Anti-Patterns to Avoid

❌ **Don't partition by:**
- High-cardinality IDs (user_id, transaction_id) → Use bucketing instead
- Low-cardinality flags (is_active, status with 2-3 values) → Not selective enough
- Frequently updated columns → Causes partition churn
- Nullable columns with many nulls → Creates large null partition

❌ **Don't over-partition:**
```python
# Bad: Too many dimensions
.partitionedBy("year", "month", "day", "hour", "region", "product_category")
# Results in: 365 × 24 × 50 × 100 = 43.8M partitions!
```

✅ **Do this instead:**
```python
# Good: Balanced partitioning
.partitionedBy("order_date", "region")
# Results in: ~365 × 50 = ~18K partitions per year
```

---

## Iceberg-Specific Best Practices

### Hidden Partitioning (Partition Transforms)

Iceberg supports partition transforms that don't require adding partition columns to your schema:

```python
# Date transforms
.partitionedBy("days(event_timestamp)")    # Daily partitions
.partitionedBy("months(event_timestamp)")  # Monthly partitions
.partitionedBy("years(event_timestamp)")   # Yearly partitions
.partitionedBy("hours(event_timestamp)")   # Hourly partitions

# Bucketing (for high-cardinality columns)
.partitionedBy("bucket(100, user_id)")     # 100 buckets

# Truncate (for strings)
.partitionedBy("truncate(10, email)")      # First 10 characters
```

**Benefits:**
- Queries work without knowing partition structure
- Can change partitioning without rewriting data
- Automatic partition pruning

### Partition Evolution

Iceberg allows changing partition specs over time:

```python
# Initial partitioning
.partitionedBy("order_date")

# Later, add region partitioning (new data only)
spark.sql(f"""
    ALTER TABLE {table_name}
    ADD PARTITION FIELD region
""")
```

---

## Complete Example: Date-Partitioned Pipeline

```python
from pyspark.sql.functions import col, to_date
from pyspark.sql.types import DateType, DoubleType, IntegerType

# 1. Normalize column names
for col_name in df.columns:
    normalized = col_name.lower().replace(" ", "_").replace("-", "_")
    df = df.withColumnRenamed(col_name, normalized)

# 2. Cast date fields to DateType (not TimestampType)
date_columns = ["order_date", "ship_date", "delivery_date"]
for date_col in date_columns:
    if date_col in df.columns:
        df = df.withColumn(date_col, to_date(col(date_col)))

# 3. Cast other types
df = df.withColumn("order_amount", col("order_amount").cast(DoubleType()))
df = df.withColumn("quantity", col("quantity").cast(IntegerType()))

# 4. Write to Iceberg with date partitioning
df.writeTo("catalog.schema.orders") \
    .using("iceberg") \
    .partitionedBy("order_date") \
    .tableProperty("format-version", "2") \
    .tableProperty("write.parquet.compression-codec", "snappy") \
    .createOrReplace()
```

---

## Quick Reference

### Column Naming
- Lowercase: ✅ `order_date`
- Spaces → underscores: ✅ `first_name`
- Hyphens → underscores: ✅ `order_id`

### Date Types
- Business dates: ✅ `DateType`
- Event timestamps: ✅ `TimestampType`
- Naming: `*_date` or `*_at`

### Partitioning
- Date fields: ✅ First choice
- Cardinality: ✅ 100-10,000 partitions
- Query patterns: ✅ Filter in > 50% of queries
- High-cardinality IDs: ❌ Use bucketing instead

---

## When to Deviate

These are guidelines, not absolute rules. Consider deviating when:

- **Legacy compatibility**: Existing systems require specific naming
- **Regulatory requirements**: Specific timestamp precision mandated
- **Extreme scale**: Unique partitioning needs for petabyte-scale data
- **Real-time streaming**: Different optimization trade-offs

Always document deviations and the reasoning behind them.
