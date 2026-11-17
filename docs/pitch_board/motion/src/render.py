from playwright.sync_api import sync_playwright
import sys,os,time
D=os.path.dirname(os.path.abspath(__file__)); OUT=D+'/sub'; os.makedirs(OUT,exist_ok=True)
a,b=int(sys.argv[1]),int(sys.argv[2])   # диапазон кадров [a,b)
SUB=3; FPS=60
with sync_playwright() as p:
    br=p.chromium.launch(executable_path='/opt/pw-browsers/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell',args=['--no-sandbox','--allow-file-access-from-files'])
    pg=br.new_page(viewport={'width':1920,'height':1080}); pg.goto('file://'+D+'/motion.html'); pg.wait_for_timeout(1500)
    t0=time.time()
    for n in range(a,b):
        for s in range(SUB):
            t=n/FPS+(s-1)/(FPS*SUB)
            t=max(0,min(29.999,t))
            pg.evaluate(f'renderAt({t})')
            pg.screenshot(path=f'{OUT}/s{n*SUB+s:06d}.jpg',type='jpeg',quality=93)
        if (n-a)%30==0: print(n,round(time.time()-t0,1),flush=True)
    br.close()
