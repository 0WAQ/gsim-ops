from colorama import Fore, Style, init


init(autoreset=True)

def base(color, msg, *args, **kw):
    print(color + msg, *args, **kw)

def info(msg, *args, **kw):
    base(Fore.GREEN, msg, *args, **kw)

def warn(msg, *args, **kw):
    base(Fore.YELLOW, msg, *args, **kw)

def error(msg, *args, **kw):
    base(Fore.RED, msg, *args, **kw)

def highlight(msg, *args, **kw):
    base(Fore.YELLOW + Style.BRIGHT, msg, *args, **kw)

def banner(title):
    """顶部横幅"""
    line = "━" * 72
    print(Fore.CYAN + line)
    print(Fore.CYAN + f"▌ {title:^66} ▌")
    print(Fore.CYAN + line)

def bottom():
    print(Fore.CYAN + "━" * 72)