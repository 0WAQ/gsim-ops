
class Metrics:
    def __init__(self, ret, shrp, fitness):
        self.ret = ret
        self.shrp = shrp
        self.fitness = fitness

    def __repr__(self):
        return f"ret={self.ret}%, shrp={self.shrp}, fitness={self.fitness}"
    
    def __str__(self):
        return f"ret={self.ret}%, shrp={self.shrp}, fitness={self.fitness}"