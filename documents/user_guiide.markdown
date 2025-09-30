# Stewardship Atlas User Guide

## Overview

A Stewardship Atlas is a data set; a configuration for storing, processing, and sharing that data set; and a set of implementions for doing so.

More concretely it is a set of maps and documents tied to specific types of planning and implemention in a specific geographic area. Examples might include:
* Wildfire Planning and Response in a specific community
* Prioritization of projects and funding with geographic aspects - natural resource organizations, advocacy groups, etc
* Grantwriters needing to gather geospatial data and maps for proposals
* researchers and practitioners looking to work across different platforms, data formats, and toolchains in a consistent low-frction way.

For more about the philosopy and design principles behind it see our [vision.markdown](Vision Statement) and for more low level technical detail see our [atals_architecture.markdown](Architectural OVerview) and [index.html](Code Documentation).


## Examples and Use Cases
* I just want to look at a map
* I want to download a map I can use offline or in an app
* I need to add a road to an existing map
* I need to change the address on a building
* I'd like to export (some parts of) the dataset for use in another program or platform.
* I need a printable version of my Atlas
* I'd like to share a link to a view in my map.

## Access and Data Control
A Stewardship Atlas supports three simple "levels" of access to and control of data:
* Public: anyone in the world with no authentication. Useful to share in the field, not the place for anything senstive. "Read Only"
* Internal: authenticated users can view this, but not change it. "Read Only"
* Admin: only specific users can access. Can edit the data seen by the other two access classes, release new versions of the data, and generate new output artifacts. "Read/Write Access"

Currently the Stewardship does not provide user managment - there are user/password pairs your Admin users can generate and share.

## Viewing Data

### Interactively

### Files and Exports to other Platforms

## Curating and Editing Data

## Sharing Data

## Technical Details
