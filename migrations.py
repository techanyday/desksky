from app import app, db, User, Presentation
from datetime import datetime

def upgrade_db():
    """
    Upgrade database schema and migrate data
    """
    with app.app_context():
        # Create tables if they don't exist
        db.create_all()
        
        # Add any missing columns
        # This is a basic migration. For production, use proper migration tools like Alembic
        connection = db.engine.connect()
        
        # Check and add columns to User table
        inspector = db.inspect(db.engine)
        existing_columns = [col['name'] for col in inspector.get_columns('user')]
        
        if 'free_credits' not in existing_columns:
            connection.execute(db.text('ALTER TABLE "user" ADD COLUMN free_credits INTEGER DEFAULT 3'))
        
        if 'subscription_status' not in existing_columns:
            connection.execute(db.text('ALTER TABLE "user" ADD COLUMN subscription_status VARCHAR(20) DEFAULT \'free\''))
        
        if 'subscription_end' not in existing_columns:
            connection.execute(db.text('ALTER TABLE "user" ADD COLUMN subscription_end TIMESTAMP'))
        
        if 'created_at' not in existing_columns:
            connection.execute(db.text('ALTER TABLE "user" ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'))
        
        connection.commit()
        connection.close()

if __name__ == '__main__':
    upgrade_db()
