# Demand Mapping Module

`GeoCareTool.DemandMapping` — **placeholder**. Not yet implemented.

The full Care Georeferencing Tool methodology includes a *demand mapping* step that converts raw census / household data into the per-polygon population columns the `CareDeserts` module consumes (e.g. `pop_0_5`, `pop_65plus`, `pop_disability`).

For the time being, prepare demand layers manually in your tool of choice (QGIS, R, Python) and feed them to `CareDeserts.run_care_desert` as a GeoDataFrame with the required population columns. See [`CareDeserts.md`](CareDeserts.md) for the expected schema.

When the module lands, it will document its API here.

## License

This project is licensed under the MIT License.
