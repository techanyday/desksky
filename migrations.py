from app import app, db, User, Presentation
from datetime import datetime

def upgrade_db():
    """
    Upgrade database schema and migrate data
    """
    try:
        with app.app_context():
            # Create tables if they don't exist
            db.create_all()
            
            # Add any missing columns
            connection = db.engine.connect()
            inspector = db.inspect(db.engine)
            
            try:
                # Check and add columns to User table
                existing_columns = [col['name'] for col in inspector.get_columns('user')]
                
                columns_to_add = {
                    'free_credits': 'INTEGER DEFAULT 3',
                    'subscription_status': 'VARCHAR(20) DEFAULT \'free\'',
                    'subscription_end': 'TIMESTAMP',
                    'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
                }
                
                for column, type_def in columns_to_add.items():
                    if column not in existing_columns:
                        app.logger.info(f"Adding column {column} to user table")
                        connection.execute(db.text(f'ALTER TABLE "user" ADD COLUMN {column} {type_def}'))
                
                # Check and add columns to Presentation table
                existing_columns = [col['name'] for col in inspector.get_columns('presentation')]
                
                columns_to_add = {
                    'status': 'VARCHAR(20) DEFAULT \'pending\'',
                    'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
                    'google_presentation_id': 'VARCHAR(100) UNIQUE'
                }
                
                for column, type_def in columns_to_add.items():
                    if column not in existing_columns:
                        app.logger.info(f"Adding column {column} to presentation table")
                        connection.execute(db.text(f'ALTER TABLE presentation ADD COLUMN {column} {type_def}'))
                
                # Check and add columns to Payment table
                existing_columns = [col['name'] for col in inspector.get_columns('payment')]
                
                columns_to_add = {
                    'currency': 'VARCHAR(3) DEFAULT \'USD\'',
                    'status': 'VARCHAR(20) NOT NULL',
                    'payment_type': 'VARCHAR(20) NOT NULL',
                    'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
                    'reference': 'VARCHAR(100) UNIQUE'
                }
                
                for column, type_def in columns_to_add.items():
                    if column not in existing_columns:
                        app.logger.info(f"Adding column {column} to payment table")
                        connection.execute(db.text(f'ALTER TABLE payment ADD COLUMN {column} {type_def}'))
                
                connection.commit()
                app.logger.info("Database migration completed successfully")
                
            except Exception as e:
                connection.rollback()
                app.logger.error(f"Error during migration: {str(e)}")
                raise
                
            finally:
                connection.close()
                
    except Exception as e:
        app.logger.error(f"Database migration failed: {str(e)}")
        raise

if __name__ == '__main__':
    upgrade_db()
