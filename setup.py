from pathlib import Path

from setuptools import find_packages, setup

HERE = Path(__file__).parent
long_description = (HERE / "README.md").read_text(encoding="utf-8")

CORE_REQUIREMENTS = [
    # Data / arrays
    "pandas>=1.5",
    "numpy>=1.23",
    # Geospatial
    "geopandas>=0.13",
    "shapely>=2.0",            # concave_hull lives in shapely 2.x
    "osmnx>=1.6",
    "networkx>=3.0",
    "geopy>=2.3",
    # Plotting / basemaps (used by plot_grid_with_basemap)
    "matplotlib>=3.6",
    "contextily>=1.3",
    # HTTP / Google Places
    "googlemaps>=4.10",
    "requests>=2.28",
    # Misc
    "unidecode>=1.3",
    "python-dotenv>=1.0",
]

EXTRAS = {
    # Public-transport travel-time matrices via R5
    "public_transport": ["r5py>=0.1.0"],
    # Dev / test
    "dev": [
        "pytest>=7.0",
        "pytest-cov>=4.0",
        "ruff>=0.1",
    ],
}
# Convenience: `pip install GeoCareTool[all]` pulls everything
EXTRAS["all"] = sorted({pkg for group in EXTRAS.values() for pkg in group})

setup(
    name="GeoCareTool",
    version="0.3.0",
    packages=find_packages(),
    install_requires=CORE_REQUIREMENTS,
    extras_require=EXTRAS,
    author="Daniela de los Santos",
    author_email="daniela.de.los.santos@undp.org",
    description="Care Georeferencing Tool — UNDP LAC Gender Team",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/danidlsa/GeoCareTool",
    license="MIT",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Scientific/Engineering :: GIS",
        "Intended Audience :: Science/Research",
    ],
    python_requires=">=3.9",   # f-strings, dataclasses, dict-merge, from __future__ annotations
    include_package_data=True,
)
