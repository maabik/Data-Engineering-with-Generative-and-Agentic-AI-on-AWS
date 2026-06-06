# Requirements Document

## Introduction

This specification defines a data processing pipeline for Chicago ordinance violations data using AWS Glue interactive sessions in JupyterLab. The pipeline implements a medallion architecture (bronze → silver → gold) with Spark transformations and Deequ data quality validation.

## Glossary

- **Pipeline**: The complete data processing workflow from source to validated output
- **Bronze_Stage**: Raw data landing zone with minimal transformation (CSV format)
- **Silver_Stage**: Cleaned and partitioned data in Iceberg table format
- **Gold_Stage**: Fully validated, business-ready data with quality checks in Iceberg table format
- **S3_Tables**: AWS managed service for Apache Iceberg tables
- **Iceberg_Table**: Apache Iceberg table format providing ACID transactions and time travel
- **Glue_Session**: AWS Glue interactive session providing managed Spark cluster
- **Deequ**: Amazon's data quality validation library for Spark
- **Notebook**: JupyterLab notebook containing the pipeline code
- **Source_Dataset**: Chicago ordinance violations CSV file at `.kiro/data/chicago-ordinance-violations-sample.csv`
- **Table_Namespace**: S3 Tables namespace organizing related Iceberg tables

## Requirements

### Requirement 1: Glue Interactive Session Setup

**User Story:** As a data engineer, I want to configure AWS Glue interactive sessions in JupyterLab, so that I can run Spark workloads without managing infrastructure.

#### Acceptance Criteria

1. WHEN the notebook initializes, THE Notebook SHALL configure Glue session magic commands with appropriate parameters
2. WHEN the Glue session starts, THE Notebook SHALL establish connection to a managed Spark cluster
3. THE Notebook SHALL specify Python 3 as the kernel and Spark 3.x as the engine
4. WHERE Deequ is required, THE Notebook SHALL include Deequ Maven dependencies in session configuration
5. WHEN session configuration fails, THE Notebook SHALL display clear error messages with troubleshooting guidance

### Requirement 2: Bronze Stage - Data Ingestion

**User Story:** As a data engineer, I want to load the source CSV file into the bronze stage, so that I have raw data available for processing.

#### Acceptance Criteria

1. WHEN the pipeline runs, THE Pipeline SHALL read the CSV file from the local path `.kiro/data/chicago-ordinance-violations-sample.csv`
2. THE Pipeline SHALL infer the schema automatically from the CSV header row
3. WHEN reading the CSV, THE Pipeline SHALL handle quoted fields and embedded commas correctly
4. THE Pipeline SHALL preserve all original columns and data types from the source
5. WHEN the CSV file is missing or unreadable, THE Pipeline SHALL raise an informative error

### Requirement 3: Silver Stage - Iceberg Table Creation

**User Story:** As a data engineer, I want to transform raw CSV data into a managed Iceberg table using S3 Tables, so that I have ACID transactions, schema evolution, and time travel capabilities.

#### Acceptance Criteria

1. THE Pipeline SHALL create an S3 Tables namespace for the chicago violations lakehouse
2. THE Pipeline SHALL write the bronze DataFrame to an Iceberg table in the silver stage
3. THE Pipeline SHALL partition the Iceberg table by WARD column for efficient querying
4. THE Pipeline SHALL configure the Iceberg table with appropriate table properties for S3 Tables
5. WHEN duplicate records exist, THE Pipeline SHALL preserve all records in silver stage
6. THE Pipeline SHALL use the Iceberg catalog integration with AWS Glue
7. WHEN the silver table already exists, THE Pipeline SHALL append or overwrite based on configuration

### Requirement 4: Gold Stage - Data Quality Validation with Iceberg

**User Story:** As a data engineer, I want to validate data quality using Deequ checks and write to a gold Iceberg table, so that I can ensure data meets business requirements before publication.

#### Acceptance Criteria

1. THE Pipeline SHALL run Deequ data quality checks on the silver stage Iceberg table
2. THE Pipeline SHALL verify completeness checks for critical columns (ID, DOCKET_NUMBER, ADDRESS, VIOLATION_DATE)
3. THE Pipeline SHALL verify uniqueness of the ID column
4. THE Pipeline SHALL validate that IMPOSED_FINE and ADMIN_COSTS are non-negative numeric values
5. THE Pipeline SHALL validate that LATITUDE and LONGITUDE are within valid geographic ranges
6. WHEN quality checks pass, THE Pipeline SHALL write validated data to a gold stage Iceberg table
7. WHEN quality checks fail, THE Pipeline SHALL generate a detailed validation report with failure reasons
8. THE Pipeline SHALL output Deequ check results in a human-readable format
9. THE Pipeline SHALL leverage Iceberg's ACID properties to ensure atomic writes to the gold table

### Requirement 5: Pipeline Orchestration and Output

**User Story:** As a data engineer, I want the notebook to execute all stages sequentially with clear status updates, so that I can monitor pipeline progress and troubleshoot issues.

#### Acceptance Criteria

1. THE Notebook SHALL execute stages in order: bronze → silver → gold
2. WHEN each stage completes, THE Notebook SHALL display row counts and sample records
3. THE Notebook SHALL display execution time for each stage
4. WHEN any stage fails, THE Notebook SHALL halt execution and display the error with context
5. THE Notebook SHALL provide summary statistics for the final gold dataset
6. THE Notebook SHALL include markdown cells documenting each stage's purpose and logic

### Requirement 6: Data Schema and Type Handling

**User Story:** As a data engineer, I want proper data type handling for all columns, so that downstream analytics have correctly typed data.

#### Acceptance Criteria

1. THE Pipeline SHALL cast date columns (HEARING_DATE, LAST_MODIFIED_DATE, VIOLATION_DATE) to timestamp type
2. THE Pipeline SHALL cast numeric columns (IMPOSED_FINE, ADMIN_COSTS, LATITUDE, LONGITUDE) to appropriate numeric types
3. THE Pipeline SHALL cast WARD and STREET_NUMBER to integer type
4. THE Pipeline SHALL preserve string columns as string type
5. WHEN type casting fails for a value, THE Pipeline SHALL handle the error gracefully and log the issue

### Requirement 7: Notebook Documentation and Reusability

**User Story:** As a data engineer, I want well-documented notebook code, so that other engineers can understand and modify the pipeline.

#### Acceptance Criteria

1. THE Notebook SHALL include markdown cells explaining the medallion architecture with Iceberg tables
2. THE Notebook SHALL document all configuration parameters including S3 Tables namespace and table names
3. THE Notebook SHALL include inline comments for complex transformations
4. THE Notebook SHALL provide example outputs for each stage including Iceberg table metadata
5. THE Notebook SHALL include a cell with package versions and dependencies including Iceberg runtime
6. THE Notebook SHALL document how to query the Iceberg tables using Spark SQL

### Requirement 8: S3 Tables Configuration

**User Story:** As a data engineer, I want proper S3 Tables and Iceberg configuration, so that the pipeline integrates seamlessly with AWS managed services.

#### Acceptance Criteria

1. THE Notebook SHALL configure Spark to use the AWS Glue catalog for Iceberg tables
2. THE Notebook SHALL specify the S3 Tables bucket ARN for table storage
3. THE Notebook SHALL set appropriate Iceberg table properties for S3 Tables compatibility
4. WHEN creating tables, THE Pipeline SHALL use the `s3tablescatalog` catalog implementation
5. THE Notebook SHALL configure Iceberg format version compatible with S3 Tables
6. THE Notebook SHALL include IAM permissions documentation required for S3 Tables access
