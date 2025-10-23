# Replace a Layer with an Upload

## Overview
* From Admin page
    * find the layer you want to replace in the list at right
    * click "Clear" on row for Layer to remove all existing data
* Prepare a standard GeoJSON file
    * nice Properties to have set on each feature:
        * name
        * vector_width
     * CRS will be set to standard XXX
* click "Upload" on layer 


Remember that new data will only visible in "dynamic" Staging outlets (like the Webmap) until you regenerate an Outlet, or generate a new Version which will refresh all configured Outlets.

