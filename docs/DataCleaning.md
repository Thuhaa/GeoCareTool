# Data Cleaning Module

The data cleaning module provides functions for preprocessing datasets before they are used for modeling. This includes extracting and creating dummy variables and transforming text features.

## Functions

### `extract_types_and_create_dummies`

This function extracts types from a specified column and creates dummy variables for them.

#### Parameters
- `df` (pd.DataFrame): The DataFrame containing the data to process.
- `types_column` (str, optional): The column containing the types to extract. Default is `'types'`.
- `all_dummy_columns` (list, optional): A list of all dummy columns to ensure they are present in the final DataFrame. Default is `None`.

#### Returns
- pd.DataFrame: A DataFrame with the extracted dummy variables and the original data.

#### Example
```python
import pandas as pd

# Sample DataFrame
data = {
    'name': ['Place A', 'Place B', 'Place C'],
    'types': ['["type1", "type2"]', '["type1"]', '["type3", "type2"]']
}
df = pd.DataFrame(data)

# Extract types and create dummies
df_dummies = extract_types_and_create_dummies(df, types_column='types')
print(df_dummies)
```