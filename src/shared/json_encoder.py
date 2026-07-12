import json
from datetime import datetime, date

class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        return super().default(obj)

def to_json(obj):
    return json.dumps(obj, cls=CustomJSONEncoder, ensure_ascii=False)
