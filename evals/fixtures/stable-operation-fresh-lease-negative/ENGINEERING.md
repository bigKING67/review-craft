# Retried Operation Contract

`execute_with_retry(store, worker, request, attempts=2)` coordinates one logical
operation across multiple execution attempts:

- `store.create_operation(request)` creates the stable operation identity exactly once
  for the function invocation.
- `store.issue_lease(operation_id)` issues an attempt-scoped lease. Every attempt must
  receive a fresh lease for the same operation identity.
- A `TimeoutError` from `worker.execute(operation_id, lease)` can mean the response was
  lost. Retry up to `attempts` times without creating another operation.
- Return the confirmed worker result after recovery, and re-raise the final timeout when
  all attempts are exhausted.
