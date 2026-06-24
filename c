import io

p = 'panel.html'
s = io.open(p, encoding='utf-8').read()

amp = '&' + 'amp;'
lt = '&' + 'lt;'
gt = '&' + 'gt;'
quot = '&' + 'quot;'

# 1. 修复被破坏的 esc() 函数：整行替换
lines = s.split('\n')
for i, line in enumerate(lines):
    if 'return String(s).replace' in line:
        lines[i] = "  return String(s).replace(/&/g,'" + amp + "').replace(/</g,'" + lt + "').replace(/>/g,'" + gt + "').replace(/\"/g,'" + quot + "');"
s = '\n'.join(lines)

# 2. 清理 CDATA 残留
s = s.replace('<![CDATA[', '')
s = s.replace(']]>', '')

# 3. 清理可能的多余空行结尾
s = s.rstrip() + '\n'

io.open(p, 'w', encoding='utf-8', newline='').write(s)
print('OK, length =', len(s))