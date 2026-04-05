
class Metrics:
    def __init__(self, ret, tvr, shrp, fitness):
        self.ret = ret
        self.shrp = shrp
        self.tvr = tvr
        self.fitness = fitness

    def __repr__(self):
        return f"ret={self.ret}%, shrp={self.shrp}, tvr={self.tvr}%, fitness={self.fitness}"
    
    def __str__(self):
        return f"ret={self.ret}%, shrp={self.shrp}, tvr={self.tvr}%, fitness={self.fitness}"