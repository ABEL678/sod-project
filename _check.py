content = open('ai_checks/views.py').read()
print('FOUND' if '_selected_model' in content else 'NOT FOUND')
if '_selected_model' in content:
    idx = content.find('def _selected_model')
    print(content[idx:idx+200])
