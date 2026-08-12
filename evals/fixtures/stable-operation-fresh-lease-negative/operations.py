def execute_with_retry(store, worker, request, attempts=2):
    operation_id = store.create_operation(request)
    for attempt in range(attempts):
        lease = store.issue_lease(operation_id)
        try:
            return worker.execute(operation_id, lease)
        except TimeoutError:
            if attempt + 1 == attempts:
                raise
