# Iceberg Table Maintenance Monitor

A Python-based monitoring agent that analyzes Apache Iceberg tables stored in S3 and determines when maintenance operations are needed.

## Features

- **Monitor Iceberg table health metrics**: Track file counts, sizes, snapshots, and orphan files
- **Detect when compaction is needed**: Identify tables with too many small files
- **Identify snapshots for expiration**: Find old snapshots that can be removed
- **Find orphan files for cleanup**: Detect unreferenced data files wasting storage
- **Support for multiple tables**: Monitor multiple tables from a single agent
- **Continuous and on-demand monitoring modes**: Run as a daemon or one-time analysis

## Installation

```bash
cd iceberg-monitor
pip install -e .
```

For development with testing tools:
```bash
pip install -e ".[dev]"
```

## Quick Start

1. Create a configuration file:

```yaml
# config/my_monitor.yaml
tables:
  - s3://my-bucket/warehouse/db/events
  - s3://my-bucket/warehouse/db/users

catalog:
  type: glue

thresholds:
  compaction:
    small_file_size_mb: 128
    small_file_count_threshold: 100
    small_file_ratio_threshold: 0.5
  snapshots:
    max_snapshots: 100
    retention_hours: 168  # 7 days
  orphans:
    age_hours: 72  # 3 days

monitoring:
  mode: continuous
  interval_seconds: 3600  # 1 hour

aws:
  region: us-east-1
```

2. Run the monitor:

```bash
# Validate configuration
python -m cli --config config/my_monitor.yaml validate

# Run once
python -m cli --config config/my_monitor.yaml once

# Run continuously
python -m cli --config config/my_monitor.yaml continuous
```

## Configuration Options

### Tables
- `tables`: List of S3 paths to Iceberg tables to monitor

### Catalog
- `type`: Catalog type (`glue`, `rest`, or `hadoop`)
- `properties`: Additional catalog-specific properties

### Compaction Thresholds
- `small_file_size_mb`: Files below this size are considered "small" (default: 128MB)
- `small_file_count_threshold`: Trigger compaction if small file count exceeds this (default: 100)
- `small_file_ratio_threshold`: Trigger compaction if ratio of small files exceeds this (default: 0.5)

### Snapshot Thresholds
- `max_snapshots`: Maximum number of snapshots to retain (default: 100)
- `retention_hours`: Retain snapshots for this duration (default: 168 hours / 7 days)

### Orphan File Thresholds
- `age_hours`: Files older than this may be orphans (default: 72 hours / 3 days)

### Monitoring
- `mode`: `continuous` or `on-demand`
- `interval_seconds`: Interval between monitoring cycles in continuous mode (default: 3600)

### AWS
- `region`: AWS region for S3 access
- `s3_endpoint`: Optional S3 endpoint URL (for LocalStack testing)

## Usage Examples

### Analyze a Single Table

```bash
python -m cli --config config/single_table.yaml once
```

### Monitor Multiple Tables Continuously

```bash
python -m cli --config config/multi_table.yaml continuous
```

### Use with LocalStack for Testing

```yaml
# config/localstack.yaml
tables:
  - s3://test-bucket/warehouse/test/table1

catalog:
  type: hadoop

aws:
  region: us-east-1
  s3_endpoint: http://localhost:4566
```

## Creating Test Tables

Use the TestTableGenerator to create test tables:

```python
from test_utils.table_generator import TestTableGenerator

# Initialize generator
generator = TestTableGenerator(
    s3_bucket='test-bucket',
    table_name='test_table',
    s3_endpoint='http://localhost:4566'  # For LocalStack
)

# Create table
table = generator.create_test_table()

# Generate test scenarios
generator.create_small_files(table, num_files=200, rows_per_file=100)
generator.create_multiple_snapshots(table, num_snapshots=150)
generator.create_orphan_files(table, num_orphans=50)

# Clean up
generator.cleanup_test_table(table)
```

## Development

### Running Tests

```bash
# All tests
pytest

# Unit tests only
pytest -m unit

# Property-based tests only
pytest -m property

# Integration tests only
pytest -m integration

# With coverage
pytest --cov=src/monitor --cov-report=html
```

### Project Structure

```
iceberg-monitor/
├── src/
│   ├── monitor/
│   │   ├── __init__.py
│   │   ├── config.py          # Configuration management
│   │   ├── metrics.py         # Metric data structures
│   │   ├── analyzer.py        # Table analyzer
│   │   ├── recommendations.py # Recommendation engine
│   │   ├── reporter.py        # Report generation
│   │   └── engine.py          # Monitor engine
│   ├── test_utils/
│   │   ├── __init__.py
│   │   └── table_generator.py # Test table generator
│   └── cli.py                 # CLI interface
├── tests/
│   ├── unit/                  # Unit tests
│   ├── property/              # Property-based tests
│   └── integration/           # Integration tests
├── config/
│   └── monitor_config.yaml    # Example configuration
├── requirements.txt
├── setup.py
├── pytest.ini
└── README.md
```

## Maintenance Recommendations

The monitor generates three types of recommendations:

### 1. Compaction
Triggered when:
- Average file size falls below threshold
- Number of small files exceeds threshold
- Ratio of small files to total files exceeds threshold

**Benefit**: Improved query performance by reducing file count

### 2. Snapshot Expiration
Triggered when:
- Total snapshot count exceeds maximum
- Snapshots older than retention period exist

**Benefit**: Reduced metadata overhead and storage costs

### 3. Orphan File Cleanup
Triggered when:
- Files exist in S3 but are not referenced by any snapshot

**Benefit**: Reclaimed storage space

## Troubleshooting

### Permission Errors
Ensure your AWS credentials have permissions for:
- S3: `s3:ListBucket`, `s3:GetObject`, `s3:HeadObject`
- Glue (if using Glue catalog): `glue:GetTable`, `glue:GetDatabase`

### Table Not Found
- Verify table location in configuration
- Check catalog type matches your setup
- Ensure table exists in the catalog

### High Memory Usage
- Reduce number of tables monitored simultaneously
- Increase monitoring interval
- Consider running in on-demand mode for large tables

## License

MIT License
