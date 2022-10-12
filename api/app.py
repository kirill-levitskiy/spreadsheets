import os
import logging
import re
import json
import functools

from flask import Flask, request, make_response, jsonify
from requests_toolbelt import MultipartDecoder

from lib import manager, util, usage

logger = logging.getLogger("gluestick-api")
logging.basicConfig(level=logging.DEBUG, format='%(message)s')

app = Flask(__name__)

@app.before_first_request
def init():
    # Check if usage stats are enabled
    usage_stats = os.environ.get("GLUESTICK_USAGE_STATS", "DISABLE") == "ENABLE"

    if usage_stats:
        logger.info("""
        Anonymous usage tracking statistics are enabled.
        If you'd like to disable this, please refer to the gluestick docs https://docs.gluestick.xyz
        """)

    # Check if webhook is enabled
    webhook_url = os.environ.get("GLUESTICK_WEBHOOK_URL")

    if webhook_url is not None:
        logger.info(f"""
        You have enabled webhook updates to {webhook_url}.
        If you'd like to disable this, please refer to the gluestick docs https://docs.gluestick.xyz
        """)


#############
# CORS      #
#############
def cors(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS": # CORS preflight
            return cors_preflight()

        return func(*args, **kwargs)
    return wrapper


def cors_preflight():
    response = make_response()
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add('Access-Control-Allow-Headers', "*")
    response.headers.add('Access-Control-Allow-Methods', "*")
    return response


def corsify(data):
    response = jsonify(data)
    response.headers.add("Access-Control-Allow-Origin", "*")
    return response


#############
# FILES     #
#############
def _get_parts():
    content = request.get_data()
    content_type = request.headers['content-type']
    logger.info(f"Received ContentType Header ==> {content_type}")

    decoder = MultipartDecoder(content, content_type)

    files = []
    for p in decoder.parts:
        result = dict()

        result['content'] = p.content
        content_type = p.headers.get(b'content-type')
        if content_type is not None:
            result['content-type'] = content_type.decode("utf-8")

        content_disposition = p.headers.get(b'content-disposition')
        if content_disposition is not None:
            content_disposition = content_disposition.decode("utf-8")
            m = re.search(r'filename=\"([^\"]*)', content_disposition)
            result['content-disposition'] = content_disposition
            if m and m.group(1):
                result['filename'] = m.group(1)

        files.append(result)

    return files


@app.route('/status', methods=['GET', 'OPTIONS'])
@cors
def status():
    usage.track("Status")
    return corsify({'code': 'success'})


@app.route('/file/<user>/import', methods=['POST', 'OPTIONS'])
@cors
def do_import(user):
    body = request.json
    filename = body.get("filename")
    usage.track("Import")

    # Do the import
    manager.do_import(user, filename)

    # Trigger webhook
    util.trigger_hook(user, util.Lifecycle.DATA_EXPORTED)

    return corsify({'code': 'sucess'})


@app.route('/file/<user>/map', methods=['POST', 'OPTIONS'])
@cors
def do_mapping(user):
    body = request.json
    filename = body.get("filename")
    mapping = body.get("mapping")
    schema = body.get("schema")
    usage.track("Mapping")

    if mapping is None:
        return corsify({'code': 'error', 'message': 'No mapping Provided'}), 400

    logger.debug(f"[do_mapping]: Received mapping={json.dumps(mapping)}")

    # Generate the new file
    data = manager.do_mapping(user, filename, mapping, schema)

    # Trigger webhook
    util.trigger_hook(user, util.Lifecycle.MAPPING_COMPLETED)

    return corsify({'code': 'success', 'data': data})


@app.route('/file/<user>/validate', methods=['POST', 'OPTIONS'])
@cors
def validate_mapping(user):
    body = request.json
    filename = body.get("filename")
    mapping = body.get("mapping")
    schema = body.get("schema")
    usage.track("Validate")

    if mapping is None:
        return corsify({'code': 'error', 'message': 'No mapping Provided'}), 400

    logger.debug(f"[validate_mapping]: Received mapping={json.dumps(mapping)}")

    # Validate the mapping
    data = manager.validate_mapping(user, filename, mapping, schema)

    # Trigger webhook
    util.trigger_hook(user, util.Lifecycle.MAPPING_VALIDATION)

    return corsify({'code': 'success', 'data': data})


@app.route('/file/<user>/upload', methods=['POST', 'OPTIONS'])
@cors
def upload_file(user):
    logger.debug(f"[upload_file]: parsing payload")
    files = _get_parts()
    usage.track("Upload")

    if files is None or len(files) == 0:
        return {'code': 'error', 'message': 'No Schema Provided'}, 400

    logger.debug(f"[upload_file]: received {len(files)} files")

    # Currently restricted to one file
    file = files[0]
    filename = file.get('filename')

    # Save the file
    logger.info(f"[upload_file]: Uploading {filename}")
    manager.save_data(user, filename, file.get("content"))

    # Return the column names, and first 5 rows
    data = manager.parse_data(user, filename)

    # Trigger webhook
    util.trigger_hook(user, util.Lifecycle.FILE_UPLOADED)

    return corsify({'code': 'success', 'data': data, 'filename': filename})
