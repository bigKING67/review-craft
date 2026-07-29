# Engineering Context

`render_for_user` runs in a long-lived shared service. Both `user_id` and `template` come
from requests, and their combined value space is not bounded. The cache is intended to
reduce repeated rendering work, but process memory must remain bounded over the service
lifetime.
