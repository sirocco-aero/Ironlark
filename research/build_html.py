import pathlib,json
p=pathlib.Path(__file__).resolve().parent
s=(p/'canvas.template.html').read_text()
s=s.replace('<!--__DIRECTION__-->','')
d=(p/'dataset.json').read_text().replace('</','<\\/')
s=s.replace('/*__FONTS__*/',(p/'fonts.css').read_text()).replace('__DATA__',d).replace('/*__CSS__*/',(p/'canvas.css').read_text()).replace('/*__JS__*/',(p/'canvas.js').read_text())
licenses='\n'.join(f.read_text() for f in sorted((p/'fonts').glob('*OFL.txt')))
s=s.replace('</head>', '<!-- Embedded typefaces: copyright and OFL license notices\n'+licenses.replace('--','—')+'\n-->\n</head>')
(p/'drone-autonomy-atlas.html').write_text(s)
print(p/'drone-autonomy-atlas.html', len(s),'characters')
