"""Named durability boundaries for subprocess fault injection in offline tests."""


def boundary(name):
    """Production no-op; tests replace this function inside their own child process."""
