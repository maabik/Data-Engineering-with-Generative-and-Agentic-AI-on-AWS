-- Create table and insert sample data
CREATE TABLE sales (
    order_id INT,
    customer_name VARCHAR(100),
    product VARCHAR(100),
    price DECIMAL(10,2),
    order_date DATE
);

INSERT INTO sales VALUES
    (1, 'John Doe', 'Product A', 19.99, '2023-04-01'),
    (2, 'Jane Smith', 'Product B', 29.99, '2023-04-02'),
    (3, 'Bob Johnson', 'Product A', 19.99, '2023-04-03'),
    (4, 'Alice Williams', 'Product C', 39.99, '2023-04-04');

-- Create roles
CREATE ROLE sales_manager;
CREATE ROLE sales_rep;

-- Create policy to restrict access based on customer name
CREATE RLS POLICY sales_policy
WITH (customer_name VARCHAR(100))
USING (customer_name LIKE 'J%')
TO sales_rep;

-- Grant access to roles
GRANT SELECT ON sales TO sales_manager;
GRANT SELECT ON sales TO sales_rep;

-- Assign roles to users
CREATE USER john WITH PASSWORD 'johnpassword';
CREATE USER jane WITH PASSWORD 'janepassword';
GRANT ROLE sales_manager TO john;
GRANT ROLE sales_rep TO jane;