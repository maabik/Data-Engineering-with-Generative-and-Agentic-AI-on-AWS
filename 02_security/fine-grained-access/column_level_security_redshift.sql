-- Create table
CREATE TABLE customers (
    customer_id INT,
    name VARCHAR(100),
    email VARCHAR(100),
    phone VARCHAR(20),
    credit_card VARCHAR(20)
);

INSERT INTO customers VALUES
    (1, 'John Doe', 'john@email.com', '555-1234', '1234567890123456'),
    (2, 'Jane Smith', 'jane@email.com', '555-5678', '0987654321098765');

-- Create role
CREATE ROLE customer_service;

-- Grant column-level access
GRANT SELECT (customer_id, name, email, phone) ON customers TO customer_service;

-- Create user and assign role
CREATE USER service_rep WITH PASSWORD 'servicepassword';
GRANT ROLE customer_service TO service_rep;