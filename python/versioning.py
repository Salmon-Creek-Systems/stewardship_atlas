def atlas_path(config=None, local_path='', version='staging'):
    """
    Return the path to the atlas file, with versioning if provided
    """
    atlas_name = config['name']
    data_root = config['data_root']
    atlas_path = f"{data_root}/{atlas_name}/{version}/{local_path}"
    print(f"Atlas path: {atlas_path}")
    return atlas_path
    
    
    "
