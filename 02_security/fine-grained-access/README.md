# Fine-Grained Access Control

Three short, self-contained snippets covering the fine-grained access
patterns in chapter 02:

| File | Service | Demonstrates |
|---|---|---|
| `access_control_lake_formation.py` | Lake Formation | Column-scoped `SELECT` grant and revoke on a Glue catalog table via `lakeformation:BatchGrantPermissions` / `BatchRevokePermissions`. |
| `column_level_security_redshift.sql` | Redshift | Column-level grant — a role that can read `customer_id`, `name`, `email`, `phone` but not `credit_card`. |
| `row_level_security_redshift.sql` | Redshift | Row-level security policy that filters `sales` rows by customer name prefix per role. |

These are illustrative — they show the API and SQL shapes used in the book.
None of them are deploy scripts; they assume the table or catalog already
exists.

## Run

### Lake Formation snippet

The Python file is a reference shape, not a runnable script — replace the
`'string'` placeholders for `CatalogId` / `Id` and the IAM ARN before
executing:

```python
# Edit access_control_lake_formation.py first:
#   - CatalogId   -> your AWS account ID
#   - Id          -> any unique entry ID (e.g. uuid4().hex)
#   - DataLakePrincipalIdentifier -> the IAM user / role to grant or revoke
python3 access_control_lake_formation.py
```

Prereqs: a Glue database `employee_database` with a `salaries` table, and
the caller must be a Lake Formation data-lake admin.

### Redshift snippets

Run the SQL in Redshift Query Editor v2 or `psql` against a Redshift cluster
where the caller is a `superuser`. Both files create the demo table, the
roles, and the demo users. **The hard-coded passwords (`servicepassword`,
`johnpassword`, `janepassword`) are for the book demo only — change them
or drop the users immediately afterward.**

## Teardown

```sql
-- column_level_security_redshift.sql
DROP USER service_rep;
DROP ROLE customer_service;
DROP TABLE customers;

-- row_level_security_redshift.sql
DROP USER john;
DROP USER jane;
DROP ROLE sales_manager;
DROP ROLE sales_rep;
ALTER TABLE sales DETACH RLS POLICY sales_policy FROM sales_rep;
DROP RLS POLICY sales_policy;
DROP TABLE sales;
```

For the Lake Formation example, the snippet itself contains both the grant
and the revoke — running the second `batch_revoke_permissions` block clears
the grant. Nothing else is left behind on the AWS side.
