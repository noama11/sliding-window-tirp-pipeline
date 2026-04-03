import pyodbc
import pandas as pd

# --- CONFIGURATION ---
SERVER_NAME = r'MLS05-T\MEDLAB_DEV' 
DATABASE_NAME = 'AF_Simulation'
PATIENT_LIST = [1, 10]

# SQL Server credentials
SQL_USERNAME = 'visitors'
SQL_PASSWORD = 'visitors'

def check_sql_data():
    """
    Connects to the SQL Server and retrieves the most recent 
    entries for the specified patients.
    """
    
    # Connection string using SQL Server Authentication
    conn_str = (
        f"DRIVER={{SQL Server}};"
        f"SERVER={SERVER_NAME};"
        f"DATABASE={DATABASE_NAME};"
        f"UID={SQL_USERNAME};"
        f"PWD={SQL_PASSWORD};"
    )
    
    # SQL Query to fetch data
    query = f"""
    SELECT TOP (100) 
        [PatientID],
        [ConceptName],
        [StartTime],
        [EndTime],
        [Value]
    FROM [AF_Simulation].[dbo].[InputPatientsData]
    """
    
    try:
        print(f"Connecting to Server: {SERVER_NAME}...")
        conn = pyodbc.connect(conn_str)
        
        # Execute query and load results into a pandas DataFrame
        df = pd.read_sql(query, conn)
        
        if df.empty:
            print(f"Success: Connection established, but no data found for patients: {PATIENT_LIST}")
        else:
            print("--- SQL Query Results ---")
            # Print the table without the index numbers
            print(df.to_string(index=False))
            
        conn.close()
    except Exception as e:
        print(f"Connection Error: {e}")

if __name__ == "__main__":
    check_sql_data()