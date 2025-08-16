const express = require('express');
const { Pool } = require('pg');
const path = require('path');
const app = express();
const port = 5000;

// --- Global Middleware (Order Matters!) ---

// 1. CORS Middleware
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept');
  next();
});

// 2. Body Parser Middleware
app.use(express.json());

// Get the absolute path for serving static files
const staticPath = path.join(__dirname);
console.log(`Resolved static file path: ${staticPath}`);

// 3. Static Files Middleware - Serve frontend files
app.use(express.static(staticPath));

// Database Connection
async function connectDB() {
  const pool = new Pool({
    user: process.env.DB_USER || 'postgres',
    host: process.env.DB_HOST || 'db',
    database: process.env.DB_NAME || 'supermarket',
    password: process.env.DB_PASSWORD || 'secret',
    port: process.env.DB_PORT || 5432,
    connectionTimeoutMillis: 5000,
  });

  for (let i = 0; i < 10; i++) {
    try {
      const client = await pool.connect();
      client.release();
      console.log('Database connected!');
      return pool;
    } catch (err) {
      console.log(`Database connection failed (attempt ${i+1}/10):`, err.message);
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
  }
  
  throw new Error('Failed to connect to database after 10 attempts');
}

async function initDB(pool) {
  try {
    console.log('Initializing database...');
    
    // Create products table with barcode
    await pool.query(`
      CREATE TABLE IF NOT EXISTS products (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name VARCHAR(255) NOT NULL UNIQUE,
        price DECIMAL(10,2) NOT NULL,
        stock INT DEFAULT 100,
        barcode VARCHAR(20) UNIQUE
      );
    `);
    
    // Create orders table
    await pool.query(`
      CREATE TABLE IF NOT EXISTS orders (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        total DECIMAL(10,2) NOT NULL,
        created_at TIMESTAMP DEFAULT NOW(),
        pickup_code VARCHAR(6)
      );
    `);
    
    // Create order items table
    await pool.query(`
      CREATE TABLE IF NOT EXISTS order_items (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        order_id UUID NOT NULL REFERENCES orders(id),
        product_id UUID NOT NULL REFERENCES products(id),
        quantity INT NOT NULL
      );
    `);
    
    console.log('Tables created/verified');
    
    // Insert sample products with barcodes
    const result = await pool.query(`
      INSERT INTO products (name, price, stock, barcode)
      VALUES 
        ('Milk', 3.00, 50, '1234567890'),
        ('Bread', 2.00, 100, '1111222233'),
        ('Eggs', 3.50, 75, '6677889900'),
        ('Cheese', 4.50, 40, '4444555566'),
        ('Butter', 2.75, 30, '7777888899'),
        ('Yogurt', 1.25, 60, '3333444455'),
        ('Apple', 0.75, 120, '8888999900'),
        ('Banana', 0.50, 150, '2222333344'),
        ('Orange', 0.85, 100, '5555666677')
      ON CONFLICT (name) DO UPDATE SET
        price = EXCLUDED.price,
        stock = EXCLUDED.stock,
        barcode = EXCLUDED.barcode
      RETURNING *;
    `);
    
    console.log(`Inserted/updated ${result.rowCount} products`);
    console.log('Database schema and sample data initialized.');
  } catch (err) {
    console.error('Error initializing database:', err);
    throw err;
  }
}

// Start Server
async function startServer() {
  let pool;
  try {
    pool = await connectDB();
    await initDB(pool);
    
    // --- API Routes ---
    app.get('/api/products', async (req, res) => {
      try {
        const { rows } = await pool.query('SELECT * FROM products');
        const products = rows.map(product => ({
          ...product,
          price: Number(product.price)
        }));
        res.json(products);
      } catch (err) {
        console.error('Error fetching products:', err);
        res.status(500).json({ error: 'Failed to fetch products' });
      }
    });

    app.post('/api/orders', async (req, res) => {
      const client = await pool.connect();
      try {
        await client.query('BEGIN');
        const { items } = req.body;
        let total = 0;
        
        if (!Array.isArray(items) || items.length === 0) {
          await client.query('ROLLBACK');
          return res.status(400).json({ error: 'Invalid items data provided.' });
        }
        
        const orderItems = [];
        
        for (const item of items) {
          const productResult = await client.query(
            'SELECT id, name, price, stock FROM products WHERE id = $1 FOR UPDATE', 
            [item.productId]
          );
          
          if (productResult.rows.length === 0) {
            await client.query('ROLLBACK');
            return res.status(404).json({ error: `Product not found: ${item.productId}` });
          }
          
          const product = productResult.rows[0];
          const price = Number(product.price);
          
          if (product.stock < item.quantity) {
            await client.query('ROLLBACK');
            return res.status(400).json({ 
              error: `Insufficient stock for ${product.name}. Available: ${product.stock}, Requested: ${item.quantity}` 
            });
          }

          total += price * item.quantity;
          
          await client.query(
            'UPDATE products SET stock = stock - $1 WHERE id = $2',
            [item.quantity, item.productId]
          );
          
          orderItems.push({
            productId: product.id,
            quantity: item.quantity,
            name: product.name,
            price
          });
        }
        
        const taxRate = 0.08;
        const totalWithTax = total * (1 + taxRate);
        const pickupCode = Math.floor(100000 + Math.random() * 900000).toString().substring(0, 6);

        // Insert order
        const orderResult = await client.query(
          'INSERT INTO orders (total, pickup_code) VALUES ($1, $2) RETURNING id',
          [totalWithTax, pickupCode]
        );
        const orderId = orderResult.rows[0].id;
        
        // Insert order items
        for (const item of orderItems) {
          await client.query(
            'INSERT INTO order_items (order_id, product_id, quantity) VALUES ($1, $2, $3)',
            [orderId, item.productId, item.quantity]
          );
        }
        
        await client.query('COMMIT');
        
        res.json({ 
          orderId,
          total: totalWithTax.toFixed(2),
          pickupCode,
          items: orderItems
        });

      } catch (err) {
        await client.query('ROLLBACK');
        console.error('Error creating order:', err);
        res.status(500).json({ error: 'Failed to create order' });
      } finally {
        client.release();
      }
    });

    app.get('/api/orders/:orderId', async (req, res) => {
      try {
        const orderId = req.params.orderId;
        const orderResult = await pool.query(
          'SELECT * FROM orders WHERE id = $1', 
          [orderId]
        );
        
        if (orderResult.rows.length === 0) {
          return res.status(404).json({ error: 'Order not found' });
        }
        
        const itemsResult = await pool.query(
          `SELECT p.name, oi.quantity 
           FROM order_items oi
           JOIN products p ON oi.product_id = p.id
           WHERE oi.order_id = $1`,
          [orderId]
        );
        
        res.json({
          order: orderResult.rows[0],
          items: itemsResult.rows
        });
      } catch (err) {
        console.error('Error fetching order:', err);
        res.status(500).json({ error: 'Failed to fetch order' });
      }
    });

    app.get('/api/products/barcode/:barcode', async (req, res) => {
      try {
        const { barcode } = req.params;
        const { rows } = await pool.query(
          'SELECT * FROM products WHERE barcode = $1',
          [barcode]
        );
        
        if (rows.length === 0) {
          return res.status(404).json({ error: 'Product not found' });
        }
        
        res.json(rows[0]);
      } catch (err) {
        console.error('Error fetching product:', err);
        res.status(500).json({ error: 'Failed to fetch product' });
      }
    });

    // --- Serve Frontend ---
    // Important: This must come AFTER API routes
    app.get('/', (req, res) => {
      res.sendFile(path.join(__dirname, 'index.html'));
    });

    // Start server
    app.listen(port, '0.0.0.0', () => {
      console.log(`Backend running on port ${port}`);
    });
  } catch (err) {
    console.error('Server startup failed:', err);
    process.exit(1);
  }
}

// Execute server startup
startServer();