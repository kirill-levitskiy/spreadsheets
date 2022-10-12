import json
import logging
import os
import shutil
import subprocess
import sys

import pandas as pd

from lib import util, exec

logger = logging.getLogger("gluestick-api")

def establish_dirs():
    """
    Establish any necessary directories
    """
    # Establish directories
    data_dir = os.environ.get("GLUESTICK_DATA_DIR", "/tmp/gluestick/data")
    os.makedirs(data_dir, exist_ok=True)

    return data_dir


def validate_mapping(user, filename, mapping, schema):
    """
    Checks if mapping is valid using the validator
    """
    data_dir = establish_dirs()
    from_path = f"{data_dir}/{user}/{filename}"
    df = pd.read_csv(from_path)

    # Compute invalid mappings
    invalid = {}

    for field in schema['fields']:
        regexp = field.get("validator")
        if regexp is not None:
            col = field['col']
            # Compute against this
            df_col = df[util.get_key(mapping, col)].astype("string")
            valid = df_col.notna() & df_col.str.match(regexp)
            counts = valid.value_counts()
            percent_valid = counts.get(True, 0) / (counts.get(False, 0) + counts.get(True, 0))
            invalid_data = df_col.loc[~valid]
            invalid_rows = []

            # Serialize first 5 invalid rows
            for index, value in invalid_data.items():
                if len(invalid_rows) > 5:
                    break
                if pd.isna(value):
                    value = ''
                invalid_rows.append(value)

            # Tell them which were invalid
            invalid[col] = {
                'percent': "{:.2%}".format(percent_valid),
                'rows': invalid_rows
            }

    return invalid


def do_import(user, filename):
    """
    Send output data to configure target, if any
    """
    target = os.environ.get("GLUESTICK_TARGET")

    # Check if a target has been configured
    if target is None:
        return

    data_dir = establish_dirs()
    user_dir = f"{data_dir}/{user}"
    file_path = f"{user_dir}/{filename}".replace(".csv", "-mod.csv")

    # Prepare output dir
    output_dir = f"{user_dir}/output"
    util.del_exists(output_dir)
    os.makedirs(output_dir)

    # Write the output
    output_format = os.environ.get("GLUESTICK_TARGET_FORMAT", "csv")

    if output_format == "csv":
        # Just copy the file over
        shutil.copyfile(file_path, f"{output_dir}/{filename}")
    elif output_format == "json":
        df = pd.read_csv(file_path)
        data = df.to_dict('records')
        util.write_json_file(f"{output_dir}/{filename.replace('.csv', '.json')}", data)

    # Build the target config.json
    config_keys = [x for x in os.environ.keys() if x.startswith("GLUESTICK_TARGET_")]
    target_config = {
        'input_path': output_dir
    }

    for key in config_keys:
        # Convert the environment key into config key format (GLUESTICK_TARGET_BUCKET -> bucket)
        config_key = key[17:].lower()
        target_config[config_key] = os.environ[key].format(user=user)

    # Write the target config
    util.write_json_file(f"{user_dir}/config.json", target_config)

    # Run the target
    try:
        cmd = f"source /home/envs/{target}/bin/activate && target-{target} --config config.json"
        logger.info(f"Running Subprocess: [ {cmd} ] with path [ {sys.path} ]")

        exec.exec_process(cmd, user_dir)
    except subprocess.SubprocessError as spe:
        logger.error(f"Subprocess Failed: {spe}")
        raise spe


def do_mapping(user, filename, mapping, schema):
    """
    Update column names of filename according to mapping dict
    """
    data_dir = establish_dirs()
    from_path = f"{data_dir}/{user}/{filename}"
    to_path = from_path.replace(".csv", "-mod.csv")

    # Update column names
    # TODO: Won't work for CSV files with different separator or XLS files
    with open(from_path) as from_file, open(to_path, 'w') as to_file:
        # Get old cols
        cols = from_file.readline().rstrip().split(",")
        # Update col names
        new_cols = list(map(lambda x: mapping[x] if x in mapping else x, cols))
        # Create new header
        new_header = ",".join(new_cols) + '\n'
        # Write new header
        to_file.write(new_header)
        # Save the rest of the file
        shutil.copyfileobj(from_file, to_file)

    # Read the updated file
    df = pd.read_csv(to_path)

    # Drop any unnecessary columns
    # TODO: Is the best way of handling this?
    if len(mapping) < len(cols):
        # Get the required col names
        required_cols = list(mapping.values())
        # Only keep these
        df = df[required_cols]

    # Validation
    for field in schema['fields']:
        regexp = field.get("validator")
        if regexp is not None:
            col = field['col']
            valid = df[col].notna() & df[col].str.match(regexp)

            # Only keep valid rows
            df = df.loc[valid]

    # Write the new CSV
    df.to_csv(to_path, index=False)

    # Return a preview
    return preview_df(to_path)


def save_data(user, filename, content):
    """
    Save data to data_dir
    """
    data_dir = establish_dirs()
    user_dir = f"{data_dir}/{user}"
    os.makedirs(user_dir, exist_ok=True)

    # Save the file
    util.write_file(f"{user_dir}/{filename}", content)


def parse_data(user, filename):
    """
    Returns the columns and first 5 rows as JSON
    """
    data_dir = establish_dirs()

    # Read the first 5 rows of input data
    df = pd.read_csv(f"{data_dir}/{user}/{filename}", nrows=5)
    cols = list(df.columns)
    data = {}

    for col in cols:
        data[col] = json.loads(df[col].to_json())

    return data


def preview_df(path):
    """
    Generates a JSON preview of the final data
    """
    # Read only the first 5 rows
    df = pd.read_csv(path, nrows=5)
    cols = list(df.columns)
    rows = [cols]

    for index, row in df.iterrows():
        row_data = []

        for col in cols:
            row_data.append(row[col])

        rows.append(row_data)

    return rows
