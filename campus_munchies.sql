DROP DATABASE IF EXISTS campus_munchies;
CREATE DATABASE IF NOT EXISTS campus_munchies CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE campus_munchies;

-- Superadmins table
CREATE TABLE superadmins (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(255) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,                
    last_login TIMESTAMP NULL,                     
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Stores table
CREATE TABLE stores (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    location VARCHAR(200),
    contact_email VARCHAR(100),
    contact_phone VARCHAR(20),
    opening_hours VARCHAR(50),
    avg_rating DECIMAL(3,2) DEFAULT 0.00,
    is_active BOOLEAN DEFAULT TRUE,
    created_by INT NULL,                            
    updated_by INT NULL,                            
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_stores_created_by FOREIGN KEY (created_by)
        REFERENCES superadmins(id) ON DELETE SET NULL,
    CONSTRAINT fk_stores_updated_by FOREIGN KEY (updated_by)
        REFERENCES superadmins(id) ON DELETE SET NULL,
    INDEX idx_name (name),
    INDEX idx_stores_active (is_active)
);

-- Admins table
CREATE TABLE admins (
    id INT AUTO_INCREMENT PRIMARY KEY,
    store_id INT NOT NULL,
    username VARCHAR(255) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) DEFAULT 'admin',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
    INDEX idx_store_username (store_id, username),
    INDEX idx_admins_active (is_active)
);

-- Customers table
CREATE TABLE customers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255),
    phone VARCHAR(15),
    delivery_address VARCHAR(255),
    notifications_opt_in BOOLEAN DEFAULT TRUE,
    receive_sms BOOLEAN DEFAULT FALSE,
    receive_emails BOOLEAN DEFAULT TRUE,
    reset_token_hash VARCHAR(255),
    reset_token_expires TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_email (email),
    INDEX idx_phone (phone)
);

-- Menu items table
CREATE TABLE menu_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    store_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    category ENUM('meals','drinks','snacks','desserts','specials') NOT NULL,
    price DECIMAL(10,2) NOT NULL CHECK (price > 0),
    description TEXT,
    image_url VARCHAR(500),
    estimated_time INT DEFAULT 15 CHECK (estimated_time >= 0),
    availability BOOLEAN DEFAULT TRUE,
    stock_quantity INT DEFAULT 0 CHECK (stock_quantity >= 0),
    is_special BOOLEAN DEFAULT FALSE,
    special_price DECIMAL(10,2) NULL,
    created_by INT NULL,
    updated_by INT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES admins(id) ON DELETE SET NULL,
    FOREIGN KEY (updated_by) REFERENCES admins(id) ON DELETE SET NULL,
    CHECK (is_special = FALSE OR special_price < price),
    INDEX idx_store_category (store_id, category),
    INDEX idx_availability_stock (availability, stock_quantity),
    INDEX idx_specials (is_special, store_id),
    INDEX idx_menu_items_special (is_special, store_id)
);

-- Orders table
CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    store_id INT NOT NULL,
    order_number VARCHAR(50) NOT NULL UNIQUE,
    amount DECIMAL(10,2) NOT NULL,
    estimated_time INT NULL,
    payment_method ENUM('cash','card','paypal','mpesa') NOT NULL,
    order_type ENUM('pickup','delivery','sit_in') DEFAULT 'pickup',
    status ENUM('pending','confirmed','paid','ready','delivered','cancelled','refunded', 'completed', 'pending_refund') DEFAULT 'pending',
    delivery_address ENUM(
        'Dblock building',
        'Library building',
        'B Lab',
        'Printing center',
        'Admin building',
        'HP lap',
        'West residence gate',
        'Isaqalo east residence gate',
        'Isphetho residence gate',
        'Madiba residence gate'
    ) DEFAULT NULL,
    cancellation_reason TEXT NULL,
    cancelled_at TIMESTAMP NULL,
    delivered_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
    INDEX idx_customer_status (customer_id, status),
    INDEX idx_store_status (store_id, status),
    INDEX idx_created_at (created_at),
    INDEX idx_orders_store_status (store_id, status),
    CONSTRAINT chk_delivery_address
        CHECK (
            (order_type = 'delivery' AND delivery_address IS NOT NULL)
            OR
            (order_type IN ('pickup','sit_in') AND delivery_address IS NULL)
        )
);

-- Order items table
CREATE TABLE order_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    item_id INT NOT NULL,
    quantity INT NOT NULL CHECK (quantity > 0),
    price DECIMAL(10,2) NOT NULL CHECK (price > 0),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (item_id) REFERENCES menu_items(id) ON DELETE CASCADE,
    INDEX idx_order_item (order_id, item_id)
);

-- Transactions table
CREATE TABLE transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    customer_id INT NOT NULL,
    store_id INT NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    payment_method ENUM('cash','card','mpesa') NOT NULL,
    status ENUM('pending','completed','failed','refunded', 'pending_refund','cancelled') DEFAULT 'pending',
    provider_data JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
    INDEX idx_status_date (status, created_at),
    INDEX idx_order_number (order_id),
    INDEX idx_created_at (created_at)
);

-- Cart table
CREATE TABLE cart (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    store_id INT NOT NULL,
    item_id INT NOT NULL,
    quantity INT NOT NULL DEFAULT 1 CHECK (quantity > 0),
    notes VARCHAR(200) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
    FOREIGN KEY (item_id) REFERENCES menu_items(id) ON DELETE CASCADE,
    UNIQUE KEY unique_cart_item (customer_id, store_id, item_id),
    INDEX idx_customer_store (customer_id, store_id),
    INDEX idx_customer_item (customer_id, item_id),
    INDEX idx_store_item (store_id, item_id),
    INDEX idx_created_at (created_at),
    INDEX idx_updated_at (updated_at)
);

-- Feedback table
CREATE TABLE feedback (
    id INT AUTO_INCREMENT PRIMARY KEY,
    store_id INT NOT NULL,
    customer_id INT NOT NULL,
    rating DECIMAL(2,1) NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT,
    response TEXT, 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    UNIQUE KEY unique_feedback (store_id, customer_id),
    INDEX idx_store_customer (store_id, customer_id)
);

-- Notifications table
CREATE TABLE notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    order_id INT NULL,
    type ENUM('order_update', 'payment', 'promotion', 'feedback', 'refund', 'verification') NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL,
    INDEX idx_customer_type (customer_id, type),
    INDEX idx_read_created (is_read, created_at)
);

-- Refund requests table
CREATE TABLE refund_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    customer_id INT NOT NULL,
    store_id INT NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    status ENUM('pending','approved','denied') DEFAULT 'pending',
    reason TEXT,
    admin_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
    INDEX idx_status (status)
);

-- Password reset tokens table
CREATE TABLE password_reset_tokens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    user_type ENUM('customer', 'admin', 'superadmin') NOT NULL,
    token VARCHAR(255) UNIQUE NOT NULL,
    expires_at DATETIME NOT NULL,
    used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_token (token),
    INDEX idx_user (user_id, user_type),
    INDEX idx_expires (expires_at)
);

-- Order status history table
CREATE TABLE order_status_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    status VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    INDEX idx_order_id (order_id)
);

-- Sample Data Insertion
-- Superadmins
INSERT INTO superadmins (username, email, password_hash) VALUES
('superadmin', 'super@admin.com', '$2b$12$L.trKK7XDMfjtjN5K8gAIu8wUJiTvr.2Zwf.ZKXwphx33G9kkdlfu');

-- Stores with complete data
INSERT INTO stores (name, description, location, contact_email, contact_phone, opening_hours) VALUES
('Isivuno', 'Fresh meals and snacks for students', 'Student Center, Ground Floor', 'info@isivuno.com', '+27111234567', '08:00-17:00'),
('Khathalicious', 'Delicious home-style meals', 'Food Court, Main Campus', 'orders@khathalicious.com', '+27111234568', '07:00-16:00'),
('Kicks Pot', 'Spicy African cuisine', 'East Campus Plaza', 'contact@kickspot.com', '+27111234569', '09:00-18:00'),
('Phumlani', 'Traditional dishes and drinks', 'West Residence Area', 'phumlani@campus.com', '+27111234570', '08:30-15:30');

-- Admins with complete data
INSERT INTO admins (store_id, username, email, password_hash, role, is_active) VALUES
(1, 'admin_isivuno', 'admin@isivuno.com', '$2b$12$L.trKK7XDMfjtjN5K8gAIu8wUJiTvr.2Zwf.ZKXwphx33G9kkdlfu', 'admin', TRUE),
(2, 'admin_khatha', 'admin@khathalicious.com', '$2b$12$6n8gaBC8LLFMW.h1AsIboerd/te..NfpRBOgA8soXfbWZg9gcZMly', 'admin', TRUE),
(3, 'admin_kicks', 'admin@kickspot.com', '$2b$12$e3Yr6ouxAlfYixLg0ge2w.5wDxwteQWrroRoWLknq3.HYRNOHbHya', 'admin', TRUE),
(4, 'admin_phumlani', 'admin@phumlani.com', '$2b$12$zbWwSFJYWzq3m5k4j8gAIu8wUJiTvr.2Zwf.ZKXwphx33G9kkdlfu', 'admin', TRUE);

-- Customers
INSERT INTO customers (username, email, password_hash, phone, delivery_address, receive_sms, receive_emails) VALUES
('kwazi', 'reddiiycearh@gmail.com', '$2b$12$5QC3SbZYjlswRNRRx878q.rUSfJThqmwwtWtRIf9rJvGhN7DYVu.y', '0655285084', 'West resident gate', TRUE, TRUE),
('funwayo', 'funwayo@gmail.com', '$2b$12$L.trKK7XDMfjtjN5K8gAIu8wUJiTvr.2Zwf.ZKXwphx33G9kkdlfu', '0798765432','Dblock building', TRUE, TRUE);

-- Menu Items
INSERT INTO menu_items (store_id, name, category, price, description, image_url, estimated_time, stock_quantity, is_special) VALUES
(1, 'Burger', 'specials', 70.00, 'Juicy beef patty with fresh toppings.', 'https://images.unsplash.com/photo-1568901346375-23c9450c58cd', 15, 50, TRUE),
(1, 'Fries', 'snacks', 20.00, 'Crispy golden fries.', 'https://th.bing.com/th/id/R.ac7cd856784e8e4cd3f8fe96c6421685?rik=XEqXKpMYQkRpPg&riu=http%3a%2f%2fimages6.fanpop.com%2fimage%2fphotos%2f35300000%2fFrench-Fries-french-fries-35339396-1600-1455.jpg&ehk=z4eSUbCsgXmbFo%2bTh%2fK0k%2b3DyhA82d1FDsCsdDqcJiU%3d&risl=&pid=ImgRaw&r=0', 5, 100, FALSE),
(1, 'Coke', 'drinks', 15.00, 'Refreshing 500ml Coca-Cola.', 'https://powellsnl.ca/media/uploads/gs1/06700000462_1.png', 1, 200, FALSE),
(1, 'Mozzarella Sticks', 'snacks', 32.00, 'Breaded mozzarella sticks with marinara sauce.', 'https://sugarspunrun.com/wp-content/uploads/2021/07/Homemade-Mozzarella-Sticks-Recipe-1-of-1.jpg', 10, 100, FALSE),
(1, 'Chicken Nuggets', 'snacks', 35.00, 'Crispy chicken nuggets with dipping sauce.', 'https://tse3.mm.bing.net/th/id/OIP.3xSkJ_slXFaD97RfXvidBwHaHa?rs=1&pid=ImgDetMain&o=7&rm=3', 10, 100, FALSE),
(1, 'Chocolate Cake', 'desserts', 40.00, 'Decadent chocolate cake slice.', 'https://images.unsplash.com/photo-1578985545062-69928b1d9587', 20, 100, FALSE),
(3, 'Garlic Bread', 'snacks', 20.00, 'Toasted bread with garlic butter.', 'https://www.ambitiouskitchen.com/wp-content/uploads/2023/02/Garlic-Bread-4.jpg', 8, 100, FALSE),
(1, 'Vanilla Ice Cream', 'desserts', 25.00, 'Creamy vanilla ice cream scoop.', 'https://images.unsplash.com/photo-1563805042-7684c019e1cb', 15, 100, FALSE),
(1, 'Bottled Water', 'drinks', 10.00, 'Pure still water 500ml.', 'https://hydratlantic.co.za/wp-content/uploads/2020/12/500ml-bottle-INFO.png', 1, 200, FALSE),
(1, 'Milk Shake', 'drinks', 28.00, 'Creamy vanilla milkshake with cherry on top.', 'https://thumbs.dreamstime.com/b/creamy-milkshake-whipped-cream-cherry-served-tall-glass-cozy-diner-filled-topped-sits-counter-warm-350604923.jpg', 12, 200, FALSE),

(2, 'Mac & Cheese', 'specials', 40.00, 'Creamy macaroni with three cheeses.', 'https://th.bing.com/th/id/R.cbc4dfeb5e2ca4baa892e0265a9a9f9c?rik=Y4ci5uE7b%2buYEQ&pid=ImgRaw&r=0', 20, 50, TRUE),
(2, 'Grilled Cheese Sandwich', 'meals', 45.00, 'Melted cheese between toasted bread.', 'https://images.unsplash.com/photo-1600891964599-f61ba0e24092', 15, 50, FALSE),
(2, 'Tomato Soup', 'meals', 30.00, 'Warm tomato soup with basil.', 'https://images.unsplash.com/photo-1565299624946-b28f40a0ae38', 10, 50, FALSE),
(2, 'Lemonade', 'drinks', 18.00, 'Freshly squeezed lemonade.', 'https://images.unsplash.com/photo-1571047399553-3d5f9f4d3f6b', 5, 200, FALSE),
(2, 'Potato Wedges', 'snacks', 28.00, 'Seasoned potato wedges with sour cream.', 'https://images.unsplash.com/photo-1626700051175-6818013e1d4f', 10, 100, FALSE),
(2, 'Spring Rolls', 'snacks', 30.00, 'Crispy vegetable spring rolls.', 'https://tse3.mm.bing.net/th/id/OIP.874r88ebyAnkoHN85dz5BAHaLH?rs=1&pid=ImgDetMain&o=7&rm=3', 12, 100, FALSE),
(2, 'Samosa', 'snacks', 22.00, 'Spiced potato filled pastry.', 'https://images.unsplash.com/photo-1601050690597-df0568f70950', 8, 100, FALSE),
(2, 'Chocolate Brownie', 'desserts', 25.00, 'Rich chocolate brownie with walnuts.', 'https://images.unsplash.com/photo-1606313564200-e75d5e30476c', 15, 100, FALSE),
(2, 'Cheesecake Slice', 'desserts', 35.00, 'New York style cheesecake.', 'https://tse1.explicit.bing.net/th/id/OIP.1uQe7JpNL1TSg73lKqipBQHaKY?rs=1&pid=ImgDetMain&o=7&rm=3', 20, 100, FALSE),

(3, 'Chicken Salad', 'specials', 50.00, 'Grilled chicken on mixed greens.', 'https://thumbs.dreamstime.com/b/overhead-shot-sliced-grilled-chicken-over-mixed-greens-overhead-shot-sliced-grilled-chicken-over-mixed-greens-created-297953641.jpg', 10, 50, TRUE),
(3, 'Beef Stir Fry', 'specials', 68.00, 'Tender beef with vegetables in soy sauce.', 'https://images.unsplash.com/photo-1603360946369-dc9bb6258143', 18, 50, TRUE),
(1, 'Fish and Chips', 'meals', 70.00, 'Beer-battered fish with fries.', 'https://tse3.mm.bing.net/th/id/OIP.5_fDRXsmur8zmssqpzZWnAHaE_?rs=1&pid=ImgDetMain&o=7&rm=3', 25, 50, FALSE),
(1, 'Veggie Burger', 'meals', 60.00, 'Grilled veggie patty with toppings.', 'https://images.unsplash.com/photo-1565299624946-b28f40a0ae38', 20, 50, FALSE),
(3, 'Vegetable Stir Fry', 'meals', 42.00, 'Seasonal vegetables in garlic sauce.', 'https://images.unsplash.com/photo-1512058564366-18510be2db19', 15, 50, FALSE),
(3, 'Chicken Pasta', 'meals', 58.00, 'Penne with grilled chicken in creamy sauce.', 'https://images.unsplash.com/photo-1551183053-bf91a1d81141', 20, 50, FALSE),
(3, 'Chicken Popcorn', 'snacks', 32.00, 'Bite-sized crispy chicken.', 'https://images.unsplash.com/photo-1626645738196-c2a7c87a8f58', 10, 100, FALSE),
(1, 'Cheese Balls', 'snacks', 30.00, 'Deep-fried cheese balls with ranch.', 'https://i.pinimg.com/originals/f3/c3/b8/f3c3b8fe45ff75fc7d7f563c6c7442af.jpg', 12, 100, FALSE),
(3, 'Coca-Cola', 'drinks', 15.00, 'Refreshing Coca-Cola 500ml.', 'https://images.unsplash.com/photo-1554866585-cd94860890b7', 1, 200, FALSE),
(3, 'Green Tea', 'drinks', 22.00, 'Hot green tea with honey.', 'https://images.unsplash.com/photo-1509042239860-f550ce710b93', 5, 200, FALSE),
(3, 'Apple Pie', 'desserts', 30.00, 'Warm apple pie with cinnamon.', 'https://topteenrecipes.com/wp-content/uploads/2023/03/Cinnamon-Apple-Pie1-500x500.jpghttps://topteenrecipes.com/wp-content/uploads/2023/03/Cinnamon-Apple-Pie1-500x500.jpg', 18, 100, FALSE),
(3, 'Vanilla Cupcake', 'desserts', 20.00, 'Vanilla cupcake with frosting.', 'https://images.unsplash.com/photo-1563805042-7684c019e1cb', 12, 100, FALSE),

(1, 'Beef Wrap', 'specials', 52.00, 'Tender beef with veggies in tortilla.', 'https://media.citizen.co.za/wp-content/uploads/2022/07/sirloin-wraps.jpg', 12, 50, TRUE),
(4, 'Chicken Quesadilla', 'meals', 48.00, 'Grilled chicken and cheese in tortilla.', 'https://images.unsplash.com/photo-1565299507177-b0ac66763828', 15, 50, FALSE),
(4, 'French Fries', 'snacks', 25.00, 'Crispy golden fries.', 'https://images.unsplash.com/photo-1573080496219-bb080dd4f877', 5, 100, FALSE),
(4, 'Onion Rings', 'snacks', 28.00, 'Crispy onion rings with sauce.', 'https://gymonset.com/wp-content/uploads/2024/07/working10_Crispy_Onion_Rings_with_Dipping_Sauce_6572ece6-13af-474f-a740-7166509f6a92.png', 10, 100, FALSE),
(4, 'Orange Juice', 'drinks', 20.00, 'Freshly squeezed orange juice.', 'https://images.unsplash.com/photo-1613478223719-2ab802602423', 5, 200, FALSE),
(4, 'Iced Coffee', 'drinks', 25.00, 'Chilled coffee with milk.', 'https://images.unsplash.com/photo-1461023058943-07fcbe16d735', 8, 200, FALSE),
(4, 'Chocolate Chip Cookie', 'desserts', 15.00, 'Freshly baked cookie.', 'https://tse2.mm.bing.net/th/id/OIP.kGc5E2v8xqhEnsj7IpZ6bgHaJQ?rs=1&pid=ImgDetMain&o=7&rm=3', 10, 100, FALSE);

-- Orders - Fixed to match the app's order status and payment method enums
INSERT INTO orders (id, customer_id, store_id, order_number, amount, status, payment_method, order_type, delivery_address) VALUES
(1, 1, 1, 'ORD001', 110.00, 'pending', 'cash', 'pickup', NULL),
(2, 2, 2, 'ORD002', 80.00, 'pending', 'mpesa', 'delivery', 'Dblock building'),
(3, 1, 1, 'ORD003', 85.00, 'confirmed', 'card', 'pickup', NULL),
(4, 1, 2, 'ORD004', 95.00, 'ready', 'mpesa', 'delivery', 'Dblock building'),
(5, 1, 2, 'ORD005', 138.00, 'delivered', 'cash', 'sit_in', NULL);

-- Order Items - Fixed to use valid item IDs that exist in menu_items
INSERT INTO order_items (order_id, item_id, quantity, price) VALUES
(1, 1, 1, 70.00),
(1, 2, 2, 20.00),
(2, 9, 2, 40.00),
(3, 3, 2, 15.00),
(3, 5, 1, 35.00),
(4, 9, 1, 40.00),
(4, 10, 1, 28.00),
(4, 13, 1, 22.00),
(5, 16, 1, 68.00),
(5, 18, 1, 70.00);   

-- Transactions - Fixed to match app's transaction status enum
INSERT INTO transactions (order_id, customer_id, store_id, amount, payment_method, status) VALUES
(1, 1, 1, 110.00, 'cash', 'completed'),
(2, 2, 2, 80.00, 'mpesa', 'pending'),
(3, 1, 1, 85.00, 'card', 'completed'),
(4, 1, 2, 95.00, 'mpesa', 'completed'),
(5, 1, 2, 138.00, 'cash', 'completed');

-- Feedback - No changes needed
INSERT INTO feedback (store_id, customer_id, rating, comment) VALUES
(1, 1, 4.5, 'Great food and quick service!'),
(2, 2, 4.0, 'Delicious meals but a bit pricey.');

-- Refund Requests - No changes needed
INSERT INTO refund_requests (order_id, customer_id, store_id, amount, status, reason) VALUES
(1, 1, 1, 110.00, 'pending', 'Item not as described');

-- Notifications - Fixed to match app's notification type enum
INSERT INTO notifications (customer_id, order_id, type, message, is_read) VALUES
(1, 1, 'order_update', 'Your order ORD001 has been confirmed and is now being prepared.', FALSE),
(1, 1, 'order_update', 'Your order ORD001 is ready for pickup at Isivuno.', FALSE),
(1, 1, 'payment', 'Your payment of R110.00 for order ORD001 was successfully completed.', TRUE),
(1, NULL, 'promotion', '🔥 Special offer: Get 10% off all snacks at Isivuno this week!', FALSE),
(1, 1, 'refund', 'Your refund request for order ORD001 is currently under review.', FALSE),
(2, 2, 'order_update', 'Your order ORD002 has been confirmed and is now being prepared.', FALSE),
(2, 2, 'payment', 'Your payment of R80.00 for order ORD002 was successfully completed.', TRUE),
(2, NULL, 'promotion', '🎉 New arrivals at Khathalicious! Try our Mac & Cheese and Spring Rolls today!', FALSE);