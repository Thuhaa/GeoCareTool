from setuptools import setup, find_packages

setup(
    name='GeoCareTool',
    version='0.1',
    packages=find_packages(),
    install_requires=[
        'pandas',
        'googlemaps',
        'unidecode',
        'geopy',
        'numpy',
        'matplotlib',
        'contextily',
        'requests',
        'scikit-learn',
        'joblib',
        'shapely',
        'geopandas',
        'concurrent.futures; python_version < "3.2"',  # for older Python versions
        'glob2'  # if using glob module in Python 2
    ],
    author='Daniela de los Santos',
    author_email='daniela.de.los.santos@undp.org',
    description='Care Georeferencing Tool - UNDP LAC',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/danidlsa/GeoCareTool',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.6',
)
