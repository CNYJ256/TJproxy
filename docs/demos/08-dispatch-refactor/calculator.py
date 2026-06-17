def _add(a, b):
    return a + b

def _sub(a, b):
    return a - b

def _mul(a, b):
    return a * b

def _div(a, b):
    if b == 0:
        raise ValueError('division by zero')
    return a / b

_dispatch = {
    'add': _add,
    'sub': _sub,
    'mul': _mul,
    'div': _div,
}

def calculate(command, a, b):
    func = _dispatch.get(command)
    if func is None:
        raise ValueError(f'unknown command: {command}')
    return func(a, b)
