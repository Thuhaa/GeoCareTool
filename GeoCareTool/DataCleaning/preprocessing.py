from scipy.sparse import hstack
import pandas as pd
from joblib import load


### EXTRACT DUMMIES FROM TYPES 

def extract_types_and_create_dummies(df, types_column='types', all_dummy_columns=None):
    df['types_parsed'] = df[types_column].str.extractall(r'"([^"]+)"').groupby(level=0).agg(list)
    
    dummies = pd.get_dummies(df['types_parsed'].explode())
    dummies.columns = ['type_' + col for col in dummies.columns]
    dummies_grouped = dummies.groupby(level=0).sum()
    
    df = df.drop(columns=[types_column, 'types_parsed'])
    df_final = pd.concat([df, dummies_grouped], axis=1)
    
    # Ensure all columns from all_dummy_columns are present, add if any are missing
    if all_dummy_columns is not None:
        missing_cols = set(all_dummy_columns) - set(df_final.columns)
        for col in missing_cols:
            df_final[col] = 0
    
    # Reorder columns to match the order in all_dummy_columns, if provided
    if all_dummy_columns is not None:
        df_final = df_final.reindex(columns=df.columns.tolist() + all_dummy_columns, fill_value=0)

    return df_final


### PREPROCESSING FUNCTION

def preprocess_data(df: pd.DataFrame, columns_to_drop: list, population: str):
    """
    Preprocesses the provided dataset.
    
    Parameters:
    - df: Pandas DataFrame, the dataset to preprocess.
    - columns_to_drop: list of columns to be dropped, not including type_dummy variables.
    - population: string, population type ('children', 'disability', 'older adults').
    
    Returns:
    - Preprocessed data ready for modeling.
    """
    # Define paths based on population
    paths = {
        'children': {
            'dummy_columns': 'vectorizers/dummy_columns_list_NN.txt',
            'vectorizer': 'vectorizers/tfidf_vectorizer_NN.joblib'
        },
        'disability': {
            'dummy_columns': 'vectorizers/dummy_columns_list_discapacidad.txt',
            'vectorizer': 'vectorizers/tfidf_vectorizer_discapacidad.joblib'
        },
        'older adults': {
            'dummy_columns': 'vectorizers/dummy_columns_list_AM.txt',
            'vectorizer': 'vectorizers/tfidf_vectorizer_AM.joblib'
        }
    }
    
    # Check if population is valid
    if population not in paths:
        raise ValueError("Invalid population type. Choose from 'children', 'disability', 'older adults'.")
    
    # Load the appropriate dummy columns and vectorizer paths
    dummy_columns_path = paths[population]['dummy_columns']
    vectorizer_path = paths[population]['vectorizer']
    
    # Read all_dummies from file
    with open(dummy_columns_path, 'r') as file:
        all_dummies = file.read().splitlines()

    # Extract and create dummy variables
    df_dummies = extract_types_and_create_dummies(df, all_dummy_columns=all_dummies)

    # Load the saved TfidfVectorizer
    tfidf_vectorizer = load(vectorizer_path)

    # Prepare the text features for transformation
    X_text = df_dummies['name'].fillna('')  # Handling NaNs

    # Transform text data using the loaded, fitted TfidfVectorizer
    X_text_transformed = tfidf_vectorizer.transform(X_text)

    # Prepare dummy features (excluding columns as specified)
    X_dummies = df_dummies.drop(columns=columns_to_drop + ['name']).values  # Assuming 'name' is not wanted in dummy features

    # Concatenate transformed text data with dummy features
    # Note: Ensure X_dummies is in a format compatible for concatenation (e.g., a dense array if necessary)
    X_preprocessed = hstack([X_text_transformed, X_dummies])

    return X_preprocessed

# Example usage
# preprocessed_data = preprocess_data_final(df, ['column_to_drop'], 'children')
