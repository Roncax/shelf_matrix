from shelf_matrix.program.base import Program


class Solid(Program):
    fps = 20

    def draw(self, img, now):
        img.paste(tuple(int(self.ctx.params["color"][i:i + 2], 16) for i in (1, 3, 5)),
                  (0, 0, img.width, img.height))
