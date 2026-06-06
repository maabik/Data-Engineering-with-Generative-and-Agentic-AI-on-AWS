# Implementation Plan: Chicago Violations Data Pipeline

## Overview

This implementation plan creates a JupyterLab notebook that processes Chicago ordinance violations data through a medallion architecture (bronze → silver → gold) using AWS Glue interactive sessions, Apache Iceberg tables via S3 Tables, and Deequ data quality validation.

## Tasks

- [x] 1. Create notebook structure and Glue session configuration
  - Create JupyterLab notebook file `chicago_violations_pipeline.ipynb`
  - Add markdown cell explaining medallion architecture and pipeline overview
  - Add configuration cell with Glue magic commands (%glue_version, %extra_jars, %%configure)
  - Configure Deequ and Iceberg JAR dependencies
  - Set Glue version to 4.0, worker type to G.1X, 2 workers
  - Add cell to verify Spark session is active
  - Document all configuration parameters (paths, table names, S3 Tables ARN)
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 7.2, 7.5_

- [x] 2. Implement bronze stage - CSV ingestion
  - [x] 2.1 Create bronze stage code cell
    - Add markdown cell documenting bronze stage purpose
    - Define SOURCE_CSV_PATH configuration variable
    - Implement CSV reading with header=true, inferSchema=true, escape='"'
    - Add error handling for file not found with troubleshooting guidance
    - Display DataFrame schema using printSchema()
    - Display row count and sample records (first 10 rows)
    - Add execution time tracking
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 5.2, 5.3_

  - [ ]* 2.2 Write unit tests for CSV reading
    - Test reading valid CSV file
    - Test handling quoted fields with embedded commas
    - Test error handling for missing file
    - Test schema inference produces expected column types
    - _Requirements: 2.1, 2.2, 2.3, 2.5_

  - [ ]* 2.3 Write property test for column preservation
    - **Property 2: Column Preservation**
    - **Validates: Requirements 2.4**
    - Generate DataFrames with random column names
    - Verify all input columns exist in bronze DataFrame
    - _Requirements: 2.4_

- [x] 3. Configure S3 Tables and Iceberg catalog
  - Add markdown cell explaining S3 Tables and Iceberg integration
  - Define catalog configuration variables (CATALOG_NAME, S3_TABLES_BUCKET_ARN)
  - Configure Spark catalog properties for Iceberg with GlueCatalog
  - Set spark.sql.catalog.{catalog}.catalog-impl to GlueCatalog
  - Set spark.sql.catalog.{catalog}.warehouse to S3 Tables bucket ARN
  - Set spark.sql.catalog.{catalog}.io-impl to S3FileIO
  - Create namespace if not exists using Spark SQL
  - Add markdown cell documenting required IAM permissions
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 3.1, 3.6_

- [x] 4. Implement silver stage - schema transformation and Iceberg table creation
  - [x] 4.1 Create schema transformation logic
    - Add markdown cell documenting silver stage purpose and transformations
    - Normalize column names (replace spaces with underscores, lowercase)
    - Cast date columns (hearing_date, last_modified_date, violation_date) to TimestampType
    - Cast numeric columns (imposed_fine, admin_costs, latitude, longitude) to DoubleType
    - Cast ward and street_number to IntegerType
    - Handle null values from failed casts with warning messages
    - Display transformed schema and sample records
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 4.2 Write transformed data to Iceberg table
    - Define SILVER_TABLE_NAME variable
    - Use writeTo() API with .using("iceberg")
    - Configure partitionedBy("ward")
    - Set table properties: format-version=2, compression codec
    - Use createOrReplace() mode
    - Display Iceberg table metadata (snapshots, partition spec)
    - Query table to verify data was written
    - Display row count and execution time
    - _Requirements: 3.2, 3.3, 3.4, 3.7, 5.2, 5.3_

  - [ ]* 4.3 Write unit tests for schema transformation
    - Test date column casting with valid dates
    - Test numeric column casting with valid numbers
    - Test column name normalization
    - Test handling of null values during casting
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 4.4 Write property test for data preservation
    - **Property 1: Data Preservation Through Pipeline**
    - **Validates: Requirements 2.4, 3.5**
    - Generate random DataFrames with varying row counts
    - Verify row count is preserved from bronze to silver
    - _Requirements: 2.4, 3.5_

  - [ ]* 4.5 Write property test for schema transformation correctness
    - **Property 3: Schema Transformation Correctness**
    - **Validates: Requirements 6.1, 6.2, 6.4**
    - Generate DataFrames with mixed types
    - Verify date columns become TimestampType
    - Verify numeric columns become numeric types
    - Verify string columns remain StringType
    - _Requirements: 6.1, 6.2, 6.4_

  - [ ]* 4.6 Write unit tests for Iceberg table operations
    - Test table creation with correct properties
    - Test partition specification is set correctly
    - Test table metadata can be queried
    - _Requirements: 3.2, 3.3, 3.4_

  - [ ]* 4.7 Write property test for partition metadata consistency
    - **Property 5: Partition Metadata Consistency**
    - **Validates: Requirements 3.3**
    - Generate Iceberg tables with various configurations
    - Verify partition spec includes ward column
    - _Requirements: 3.3_

- [ ] 5. Checkpoint - Verify bronze and silver stages work end-to-end
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Implement Deequ data quality validation
  - [ ] 6.1 Configure Deequ validation suite
    - Add markdown cell explaining data quality validation approach
    - Import Deequ classes (Check, CheckLevel, VerificationSuite)
    - Read silver Iceberg table into DataFrame
    - Create Check with CheckLevel.Error
    - Add completeness checks for id, docket_number, address, violation_date
    - Add uniqueness check for id column
    - Add non-negative checks for imposed_fine and admin_costs
    - Add range checks for latitude (-90 to 90) and longitude (-180 to 180)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [ ] 6.2 Execute validation and handle results
    - Run VerificationSuite on silver DataFrame
    - Parse VerificationResult to check status
    - Display validation results in human-readable format
    - If validation passes: display success message with check summary
    - If validation fails: display detailed failure report with constraint violations
    - Halt pipeline execution if validation fails (raise exception)
    - _Requirements: 4.7, 4.8, 5.4_

  - [ ]* 6.3 Write unit tests for Deequ validation
    - Test validation passes with valid data
    - Test validation fails with invalid data (negative fines, out-of-range coordinates)
    - Test validation report generation
    - Test pipeline halts on validation failure
    - _Requirements: 4.1, 4.6, 4.7, 4.8_

  - [ ]* 6.4 Write property test for Deequ suite completeness
    - **Property 4: Deequ Validation Suite Completeness**
    - **Validates: Requirements 4.2, 4.4, 4.5**
    - Generate various VerificationSuite configurations
    - Verify all required checks are present (completeness, uniqueness, range)
    - _Requirements: 4.2, 4.4, 4.5_

- [ ] 7. Implement gold stage - validated data publication
  - Add markdown cell documenting gold stage purpose
  - Define GOLD_TABLE_NAME variable
  - Add conditional logic: only write if validation passed
  - Write validated DataFrame to gold Iceberg table
  - Use same partitioning strategy (by ward)
  - Set table properties for gold table
  - Display gold table metadata and statistics
  - Show sample records from gold table
  - Add execution time tracking
  - _Requirements: 4.6, 4.9, 5.2, 5.3_

- [ ]* 7.1 Write unit tests for gold stage
  - Test gold table is created only when validation passes
  - Test gold table has correct schema and partitioning
  - Test gold table contains expected data
  - _Requirements: 4.6_

- [ ] 8. Add pipeline summary and query examples
  - Add markdown cell with pipeline execution summary
  - Display total row counts for each stage (bronze, silver, gold)
  - Display total execution time for entire pipeline
  - Add markdown cell with Spark SQL query examples for Iceberg tables
  - Include example queries: SELECT *, filtering by ward, aggregations
  - Include example of time travel query (reading previous snapshot)
  - Document how to view table history and snapshots
  - _Requirements: 5.5, 5.6, 7.1, 7.6_

- [ ] 9. Add comprehensive notebook documentation
  - Review all markdown cells for clarity and completeness
  - Ensure each code cell has explanatory markdown above it
  - Add inline comments for complex transformations
  - Verify configuration parameters are documented at top
  - Add troubleshooting section for common errors
  - Add section on monitoring and observability
  - _Requirements: 7.1, 7.2, 7.3, 7.4_

- [ ] 10. Final checkpoint - End-to-end pipeline validation
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties
- Unit tests validate specific examples and edge cases
- The notebook should be executable from top to bottom without errors
- All AWS resources (S3 Tables, Glue catalog) must be configured before running
