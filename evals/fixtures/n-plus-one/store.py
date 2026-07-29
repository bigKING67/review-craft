class CustomerStore:
    def fetch_customer(self, customer_id):
        """Fetch one customer with one external read."""
        raise NotImplementedError

    def fetch_customers(self, customer_ids):
        """Fetch many customers with one supported batch read."""
        raise NotImplementedError
