"""Local desktop workspace. No server or account is required."""
import asyncio
from copy import deepcopy
from datetime import datetime
import os
from pathlib import Path
import queue
import threading
import uuid
from . import local_test as local

FIND_LABELS = {'ID': 'Element name', 'TEST_ID': 'Testing label', 'ARIA_LABEL': 'Accessible label', 'TEXT': 'Visible text', 'CSS_SELECTOR': 'Page path', 'XPATH': 'Document path'}
STATUS = {'PASSED': 'Passed', 'FAILED': 'Failed', 'SKIPPED': 'Skipped', 'CANCELLED': 'Cancelled', 'COMPLETED': 'Passed'}


def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog
    root = tk.Tk(); root.title('AssureDen — Record and Run Tests'); root.geometry('1040x700'); root.minsize(800, 550)
    name = tk.StringVar(value='My test'); url = tk.StringVar(value='https://'); status = tk.StringVar(value='Record a test or open a saved test. No connection to a server is needed.')
    screenshots = tk.BooleanVar(value=True)
    messages = queue.Queue(); stop = threading.Event()
    document = {'schema_version': 1, 'name': 'My test', 'steps': [], 'warnings': []}
    current_file = None; results_folder = None; busy = False; dirty = False
    controls = []
    frame = ttk.Frame(root, padding=16); frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Record and Run Tests', font=('Segoe UI', 20)).pack(anchor='w')
    ttk.Label(frame, text='Record actions, review steps, add checks and try your test.').pack(anchor='w', pady=(0, 12))
    fields = ttk.Frame(frame); fields.pack(fill='x')
    ttk.Label(fields, text='Test name').grid(row=0, column=0, sticky='w')
    name_entry = ttk.Entry(fields, textvariable=name); name_entry.grid(row=1, column=0, sticky='ew', padx=(0, 12))
    ttk.Label(fields, text='Website address').grid(row=0, column=1, sticky='w')
    url_entry = ttk.Entry(fields, textvariable=url); url_entry.grid(row=1, column=1, sticky='ew')
    fields.columnconfigure(0, weight=1); fields.columnconfigure(1, weight=2)
    controls.extend([name_entry, url_entry])
    toolbar = ttk.Frame(frame); toolbar.pack(fill='x', pady=12)
    tree = ttk.Treeview(frame, columns=('number', 'action', 'element', 'value', 'check', 'result'), show='headings', selectmode='browse')
    for key, label, width in [('number', 'Step', 45), ('action', 'Action', 110), ('element', 'Element', 210), ('value', 'Value', 180), ('check', 'Expected Result', 170), ('result', 'Result', 90)]:
        tree.heading(key, text=label); tree.column(key, width=width)
    tree.pack(fill='both', expand=True)
    editbar = ttk.Frame(frame); editbar.pack(fill='x', pady=10)
    warning = tk.StringVar(); ttk.Label(frame, textvariable=warning, wraplength=950).pack(anchor='w')
    ttk.Checkbutton(frame, text='Take screenshot when step fails', variable=screenshots).pack(anchor='w')
    ttk.Label(frame, textvariable=status, wraplength=950).pack(anchor='w', pady=8)

    def refresh():
        tree.delete(*tree.get_children())
        for i, step in enumerate(document['steps']):
            value = 'Saved setting' if step.get('is_secret') else step.get('input_value', '')
            active = [x for x in step.get('locators', []) if x.get('is_active', True)]
            target = next((x['value'] for kind in ('TEXT', 'ARIA_LABEL', 'TEST_ID', 'ID') for x in active if x['type'] == kind), active[0]['value'] if active else 'Page')
            tree.insert('', 'end', iid=str(i), values=(i + 1, local.ACTIONS[step['action']], target, value, local.CHECKS.get(step.get('check', {}).get('kind', ''), 'No check'), ''))
        warning.set(' '.join(document.get('warnings', [])))

    def changed(*_):
        nonlocal dirty
        dirty = True
    name.trace_add('write', changed)

    def discard():
        return not dirty or messagebox.askyesno('Unsaved changes', 'Continue without saving your changes?', parent=root)

    def save():
        nonlocal current_file, dirty
        document['name'] = name.get().strip()
        target = current_file or filedialog.asksaveasfilename(parent=root, title='Save test', defaultextension='.json', filetypes=[('Saved tests', '*.json')])
        if not target: return False
        try:
            local.save_test(target, document); current_file = Path(target); dirty = False
            status.set('Saved: ' + str(current_file)); return True
        except Exception as exc:
            messagebox.showerror('Could not save', str(exc), parent=root); return False

    def open_test():
        nonlocal document, current_file, dirty
        if not discard(): return
        path = filedialog.askopenfilename(parent=root, title='Open test', filetypes=[('Saved tests', '*.json')])
        if not path: return
        try:
            loaded = local.load_test(path)
            document = loaded; current_file = Path(path); name.set(document['name'])
            url.set(document['steps'][0].get('input_value', '') if document['steps'][0]['action'] == 'NAVIGATE' else '')
            dirty = False; refresh(); status.set('Opened: ' + str(current_file))
        except Exception as exc: messagebox.showerror('Could not open', str(exc), parent=root)

    def launch(operation, action):
        nonlocal busy
        busy = True; stop.clear()
        for control in controls: control.configure(state='disabled')
        stop_button.configure(state='normal', text='Stop Recording' if operation == 'recording' else 'Cancel Test Run')
        status.set('Opening Microsoft Edge…')
        def task():
            try: messages.put((operation, asyncio.run(action())))
            except Exception: messages.put(('error', 'Could not finish. Check that Microsoft Edge is installed and the website can be opened.'))
            finally: messages.put(('done', None))
        threading.Thread(target=task, daemon=True).start()

    def record_test():
        if not name.get().strip():
            messagebox.showerror('Test name needed', 'Enter a test name.', parent=root); return
        if not discard(): return
        try: local.plan({'schema_version': 1, 'name': name.get(), 'steps': [{'action': 'NAVIGATE', 'input_value': url.get(), 'locators': []}]}, {})
        except ValueError as exc: messagebox.showerror('Check website address', str(exc), parent=root); return
        test_name, address = name.get().strip(), url.get().strip()
        launch('recording', lambda: local.record(test_name, address, stop, lambda k, v: messages.put((k, v))))

    def run_test():
        nonlocal results_folder
        if not save(): return
        settings = {}
        for key in local.setting_names(document):
            value = simpledialog.askstring('Test setting', 'Enter ' + key + '. This value is used only for this test run and is not saved.', parent=root, show='*')
            if value is None: return
            settings[key] = value
        frozen = deepcopy(document)
        try: local.plan(frozen, settings)
        except ValueError as exc: messagebox.showerror('Check test settings', str(exc), parent=root); return
        results_folder = current_file.parent / (current_file.stem + '.results') / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
        refresh(); take_pictures = screenshots.get()
        launch('run', lambda: local.run(frozen, settings, results_folder, stop, lambda k, v: messages.put((k, v)), screenshots=take_pictures))

    def edit_step():
        nonlocal dirty
        if busy or not tree.selection(): return
        index = int(tree.selection()[0]); step = deepcopy(document['steps'][index])
        window = tk.Toplevel(root); window.title('Edit Step'); window.transient(root); window.grab_set()
        window.geometry('720x650')
        canvas = tk.Canvas(window, highlightthickness=0)
        scrollbar = ttk.Scrollbar(window, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y'); canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(canvas, padding=16)
        content = canvas.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(content, width=event.width))
        action = tk.StringVar(value=local.ACTIONS[step['action']]); value = tk.StringVar(value=step.get('input_value', ''))
        check = tk.StringVar(value=local.CHECKS[step.get('check', {}).get('kind', '')]); expected = tk.StringVar(value=step.get('check', {}).get('expected', ''))
        ttk.Label(body, text='Action').pack(anchor='w'); ttk.Combobox(body, textvariable=action, values=list(local.ACTIONS.values()), state='readonly').pack(fill='x')
        ttk.Label(body, text='Value or named setting, such as {{PASSWORD}}').pack(anchor='w', pady=(8,0)); ttk.Entry(body, textvariable=value, width=65).pack(fill='x')
        ttk.Label(body, text='How to Find It (tried from top to bottom)').pack(anchor='w', pady=(10,0))
        finder_values = []
        for locator in step.get('locators', []):
            row = ttk.Frame(body); row.pack(fill='x', pady=2)
            text = tk.StringVar(value=locator['value']); enabled = tk.BooleanVar(value=locator.get('is_active', True))
            ttk.Checkbutton(row, text=locator.get('notes') or FIND_LABELS[locator['type']], variable=enabled).pack(side='left')
            ttk.Entry(row, textvariable=text, width=45).pack(side='right', fill='x', expand=True)
            finder_values.append((locator, text, enabled))
        ttk.Label(body, text='Expected Result').pack(anchor='w', pady=(10,0)); ttk.Combobox(body, textvariable=check, values=list(local.CHECKS.values()), state='readonly').pack(fill='x')
        ttk.Entry(body, textvariable=expected).pack(fill='x', pady=4)
        def apply():
            nonlocal dirty
            step['action'] = next(k for k,v in local.ACTIONS.items() if v == action.get())
            step['input_value'] = value.get()
            step['locators'] = [{**locator, 'value': text.get(), 'is_active': enabled.get()} for locator,text,enabled in finder_values]
            step['check'] = {'kind': next(k for k,v in local.CHECKS.items() if v == check.get()), 'expected': expected.get()}
            proposed = deepcopy(document); proposed['steps'][index] = step
            try: local.validate(proposed)
            except ValueError as exc: messagebox.showerror('Check this step', str(exc), parent=window); return
            document['steps'][index] = step; dirty = True; refresh(); window.destroy()
        ttk.Button(body, text='Save Step', command=apply).pack(pady=(10,0))

    def remove_step():
        nonlocal dirty
        if tree.selection():
            del document['steps'][int(tree.selection()[0])]; dirty = True; refresh()

    def move_step(direction):
        nonlocal dirty
        if not tree.selection(): return
        index = int(tree.selection()[0]); target = index + direction
        if not 0 <= target < len(document['steps']): return
        document['steps'][index], document['steps'][target] = document['steps'][target], document['steps'][index]
        dirty = True; refresh(); tree.selection_set(str(target))

    def add_button(parent, label, command):
        button = ttk.Button(parent, text=label, command=command); button.pack(side='left', padx=(0,6)); controls.append(button)
    for label, action in [('Open Test', open_test), ('Save Test', save), ('Record a Test', record_test), ('Run Test', run_test)]: add_button(toolbar, label, action)
    stop_button = ttk.Button(toolbar, text='Stop Recording', state='disabled', command=stop.set); stop_button.pack(side='left')
    for label, action in [('Edit Step', edit_step), ('Remove Step', remove_step), ('Move Up', lambda: move_step(-1)), ('Move Down', lambda: move_step(1))]: add_button(editbar, label, action)
    def show_results():
        if results_folder and results_folder.exists(): os.startfile(str(results_folder))
        else: messagebox.showinfo('Results', 'Run a test first to create results.', parent=root)
    add_button(editbar, 'Open Results Folder', show_results)
    tree.bind('<Double-1>', lambda _: edit_step())

    def drain():
        nonlocal busy, document, current_file, dirty
        while not messages.empty():
            kind, data = messages.get()
            if kind == 'status': status.set(data)
            elif kind == 'recording':
                document = data; current_file = None; dirty = True; refresh(); status.set('Recording ready. Review the steps and add an Expected Result before running.')
            elif kind == 'result':
                item = str(data['position'] - 1)
                if tree.exists(item): tree.set(item, 'result', STATUS.get(data['status'], data['status']))
                if data.get('error_message'): warning.set('Step ' + str(data['position']) + ': ' + data['error_message'])
            elif kind == 'run': status.set('Test Run: ' + STATUS.get(data['status'], data['status']) + '. Results are saved next to your test.')
            elif kind == 'error': messagebox.showerror('Could not finish', data, parent=root); status.set(data)
            elif kind == 'done':
                busy = False
                for control in controls: control.configure(state='normal')
                stop_button.configure(state='disabled')
        root.after(100, drain)
    def close():
        if busy:
            stop.set(); status.set('Stopping. Close the window again when the operation finishes.'); return
        if discard(): root.destroy()
    root.protocol('WM_DELETE_WINDOW', close); root.after(100, drain); root.mainloop()


if __name__ == '__main__':
    main()
