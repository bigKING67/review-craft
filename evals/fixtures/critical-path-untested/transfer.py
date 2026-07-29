def format_amount(cents):
    return f"{cents / 100:.2f}"


def transfer(repository, source, destination, cents):
    repository.debit(source, cents)
    repository.credit(destination, cents)
    repository.commit()
