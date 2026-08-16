CREATE TABLE IF NOT EXISTS tenants (
    tenant_id VARCHAR(100) NOT NULL,
    display_name VARCHAR(200) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (tenant_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS products (
    tenant_id VARCHAR(100) NOT NULL,
    product_id VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    part_number VARCHAR(100) NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    PRIMARY KEY (tenant_id, product_id),
    KEY idx_products_tenant_name (tenant_id, name),
    KEY idx_products_tenant_part (tenant_id, part_number),
    CONSTRAINT fk_products_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS suppliers (
    tenant_id VARCHAR(100) NOT NULL,
    supplier_id VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (tenant_id, supplier_id),
    KEY idx_suppliers_tenant_name (tenant_id, name),
    CONSTRAINT fk_suppliers_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS inventory (
    tenant_id VARCHAR(100) NOT NULL,
    product_id VARCHAR(100) NOT NULL,
    available_quantity DECIMAL(18, 4) NOT NULL DEFAULT 0,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (tenant_id, product_id),
    CONSTRAINT fk_inventory_product FOREIGN KEY (tenant_id, product_id)
        REFERENCES products(tenant_id, product_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS quotations (
    tenant_id VARCHAR(100) NOT NULL,
    quotation_id VARCHAR(100) NOT NULL,
    product_id VARCHAR(100) NOT NULL,
    supplier_id VARCHAR(100) NOT NULL,
    unit_price DECIMAL(18, 4) NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'CNY',
    valid_until DATETIME(6) NULL,
    PRIMARY KEY (tenant_id, quotation_id),
    KEY idx_quotations_lookup (tenant_id, product_id, supplier_id, valid_until),
    CONSTRAINT fk_quotations_product FOREIGN KEY (tenant_id, product_id)
        REFERENCES products(tenant_id, product_id),
    CONSTRAINT fk_quotations_supplier FOREIGN KEY (tenant_id, supplier_id)
        REFERENCES suppliers(tenant_id, supplier_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS procurement_tasks (
    tenant_id VARCHAR(100) NOT NULL,
    task_id VARCHAR(100) NOT NULL,
    created_by VARCHAR(100) NOT NULL,
    status VARCHAR(40) NOT NULL,
    request_json JSON NOT NULL,
    version INT NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (tenant_id, task_id),
    KEY idx_tasks_tenant_status (tenant_id, status, updated_at),
    CONSTRAINT fk_tasks_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS outbox_events (
    event_id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id VARCHAR(100) NOT NULL,
    task_id VARCHAR(100) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    payload_json JSON NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    dispatched_at DATETIME(6) NULL,
    PRIMARY KEY (event_id),
    KEY idx_outbox_dispatch (status, created_at),
    CONSTRAINT fk_outbox_task FOREIGN KEY (tenant_id, task_id)
        REFERENCES procurement_tasks(tenant_id, task_id)
) ENGINE=InnoDB;
