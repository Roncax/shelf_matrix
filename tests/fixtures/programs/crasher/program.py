from shelf_matrix.program.base import Program


class Crasher(Program):
    def __init__(self, ctx):
        raise RuntimeError("crasher fixture: boom")
