import os
import re
import shutil
from compileall import compile_dir
from os import path as ospath
from threading import Thread

from lk_logger import lk
from lk_utils import filesniff
from lk_utils.read_and_write import dumps, loads


class GlobalConf:
    # 对于一些修改较为频繁, 但用途很小的参数, 放在了这里. 您可以按需修改
    # 使用 Pycharm 的搜索功能查看它在哪里被用到
    create_checkup_tools = True  # True|False
    #   如果您在实现增量更新 (仅发布 src 文件夹), 请设为 False
    create_venv_shell = True
    #   如果您在实现增量更新 (仅发布 src 文件夹), 请设为 False
    create_launcher = True  # True|False


def full_build(file):
    GlobalConf.create_checkup_tools = True
    GlobalConf.create_venv_shell = True
    GlobalConf.create_launcher = True
    process_pyproject(file)


def min_build(file):
    GlobalConf.create_checkup_tools = False
    GlobalConf.create_venv_shell = False
    GlobalConf.create_launcher = True  # True(suggest)|False
    process_pyproject(file)


def process_pyproject(pyproj_file):
    """
    Args:
        pyproj_file: pyproject.json.
        
    References:
        docs/pyproject template.md
        pyportable_installer/template/pyproject.json
    """
    
    def pretty_path(p):
        return p.replace('\\', '/')
    
    pyproj_dir = pretty_path(ospath.abspath(f'{pyproj_file}/../'))
    lk.loga(pyproj_dir)
    
    def abspath(p: str):
        if p == '': return ''
        if p[1] == ':': return pretty_path(p)  # FIXME: doesn't work in macOS
        return pretty_path(ospath.abspath(f'{pyproj_dir}/{p}'))
    
    def relpath(p: str, start):
        # 注意: pyproj_dir 与 start 可能是不同的. 在 conf_i 中, 所有相对路径均是
        # 指相对于 pyproj_dir 的, 而在本函数中, 相对路径的计算是相对于
        # conf_i['build']['proj_dir'] 的父目录而言 (该目录相当于 `_apply_config
        # :vars:srcdir`)
        # 当 pyproject.json 位于要打包的项目源代码的父目录时, pyproj_dir 与
        # start 相同 (这是推荐的放置位置); 如果 pyproject.json 放置不当, 比如放
        # 在了其他目录, 那么就会不同.
        if p == '': return ''
        if p[1] == ':': return pretty_path(ospath.relpath(p, start))
        return pretty_path(ospath.relpath(abspath(p), start))
    
    # --------------------------------------------------------------------------
    
    conf_i = loads(pyproj_file)
    conf_o = loads('template/pyproject.json')  # type: dict
    #   conf_o has the same struct with conf_i
    
    # check conf_i
    assert conf_i['app_version']
    
    if pyver := conf_i['build']['required']['python_version']:
        assert pyver.replace('.', '').isdigit() and len(pyver.split('.')) == 2
    
    proj_dir = abspath(conf_i['build']['proj_dir'])
    proj_dir_parent = ospath.dirname(proj_dir)
    #   该值相当于 `_apply_config:vars:srcdir`
    # # del proj_dir
    
    # --------------------------------------------------------------------------
    
    # assign conf_i to conf_o
    conf_o['app_name'] = conf_i['app_name']
    conf_o['app_version'] = conf_i['app_version']
    conf_o['description'] = conf_i['description']
    conf_o['author'] = conf_i['author']
    
    conf_o['build']['proj_dir'] = abspath(conf_i['build']['proj_dir'])
    conf_o['build']['dist_dir'] = abspath(conf_i['build']['dist_dir'].format(
        app_name=conf_i['app_name'],
        app_name_lower=conf_i['app_name'].lower().replace(' ', '_'),
        app_version=conf_i['app_version']
    ))
    conf_o['build']['icon'] = abspath(
        conf_i['build']['icon'] or ospath.abspath('template/python.ico')
    )
    conf_o['build']['readme'] = abspath(conf_i['build']['readme'])
    conf_o['build']['module_paths'] = [
        relpath(p, proj_dir_parent) for p in conf_i['build']['module_paths']
    ]
    conf_o['build']['attachments'] = {
        abspath(k): v for (k, v) in conf_i['build']['attachments'].items()
    }
    conf_o['build']['enable_console'] = conf_i['build']['enable_console']
    
    conf_o['build']['target'] = conf_i['build']['target']
    conf_o['build']['target']['file'] = relpath(
        conf_i['build']['target']['file'].format(
            dist_dir=conf_o['build']['dist_dir']
        ),
        proj_dir_parent
    )
    
    conf_o['build']['required'] = conf_i['build']['required']
    conf_o['build']['required']['venv'] = abspath(
        conf_i['build']['required']['venv']
    )
    
    conf_o['note'] = conf_i['note']
    
    # run conf_o
    # lk.logp(conf_o)
    _apply_config(conf_o['app_name'], **conf_o['build'])
    dumps(conf_o, conf_o['build']['dist_dir'] + '/build/manifest.json')


def _apply_config(app_name, proj_dir, dist_dir, target, required,
                  module_paths=None, attachments=None, **misc):
    """
    
    Args:
        app_name (str)
        proj_dir (str): abspath of dir
        dist_dir (str): abspath of dir
        target (dict)
        module_paths (list): see `_create_launcher()`
        attachments (dict): 附件和资产
        required (dict)
        **misc:
            readme: abspath *.md|*.html|*.pdf|...
            icon: abspath *.icon
            enable_console: True|False
    """
    # adjust args
    if module_paths is None: module_paths = []
    if attachments is None: attachments = {}
    readme = misc.get('readme', '')
    
    # precheck
    _precheck_args(
        proj_dir, dist_dir, readme, attachments, required['python_version']
    )
    
    # if output dirs not exist, create them
    rootdir, srcdir, buildir = dist_dir, f'{dist_dir}/src', f'{dist_dir}/build'
    #   rootdir: 'root directory'
    #   srcdir: 'source code directory'
    #   buildir: 'build (noun.) directory'
    filesniff.force_create_dirpath(srcdir)
    filesniff.force_create_dirpath(buildir)
    
    # --------------------------------------------------------------------------
    
    dirs_to_compile = []
    
    dirs_to_compile.extend(_copy_sources(proj_dir, srcdir))
    
    dirs_to_compile.extend(_copy_assets(attachments, srcdir))
    
    if GlobalConf.create_checkup_tools:
        _copy_checkup_tool(buildir)
    
    _create_launcher(
        app_name, misc.get('icon'), target, rootdir,
        pyversion=required['python_version'],
        extend_sys_paths=module_paths,
        enable_venv=required['enable_venv'],
        enable_console=misc.get('enable_console', True),
    )
    
    if readme:
        _create_readme(readme, rootdir)
    
    #: compile '{srcdir}/bootloader.py'
    _compile_py_files(srcdir, recursive=False)
    _cleanup_py_files(srcdir, recursive=False)
    # lk.loga(dirs_to_compile)
    for d in dirs_to_compile:
        _compile_py_files(d, recursive=True)
        _cleanup_py_files(d, recursive=True)
        #   you can comment this line to remain .py files for debugging purpose
    
    if required['enable_venv'] and GlobalConf.create_venv_shell:
        _copy_venv(required['venv'], f'{rootdir}/venv',
                   required['python_version'])
    
    m, n = ospath.split(rootdir)
    lk.logt("[I2501]", f'See distributed project at \n\t"{m}:0" >> {n}')


# ------------------------------------------------------------------------------

def _precheck_args(proj_dir, dist_dir, readme, attachments, pyversion):
    assert ospath.exists(proj_dir)
    
    if ospath.exists(dist_dir):
        if os.listdir(dist_dir):
            if input(
                    '警告: 要打包的目录已存在!\n'
                    '您是否确认清空目标目录以重构: "{}"\n'
                    '请注意确认删除后内容无法恢复! (y/n): '.format(dist_dir)
            ).lower() == 'y':
                shutil.rmtree(dist_dir)
                # # os.mkdir(dist_dir)
                #   we'll recreate dist_dir laterly in `_apply_config`
            else:
                raise FileExistsError
    
    assert readme == '' or ospath.exists(readme)
    
    assert all(map(ospath.exists, attachments.keys()))
    
    from .checkup.doctor import check_pyversion
    curr_ver, result = check_pyversion(*map(int, pyversion.split('.')))
    assert result is True, \
        f'prebuild 使用的 Python 版本 ({curr_ver}) ' \
        f'不符合目标编译版本 ({pyversion})!'


def _copy_sources(proj_dir, srcdir):
    """ 将 proj_dir 的内容全部拷贝到 srcdir 下.
    
    Args:
        proj_dir: see `main()`
        srcdir: 'source code dir'. 传入 `main:vars:srcdir`
    """
    yield from _copy_assets({proj_dir: 'assets,compile'}, srcdir)


def _copy_assets(attachments, srcdir):
    """
    Args:
        attachments (dict): {dir_i: type, ...}
            dir_i (str)
            type (str): 'assets'|'root_assets'|'only_folder'|'only_folders'
                |'assets,compile'|'root_assets,compile'|'only_folder,compile'
                |'only_folders,compile'
                note: 当 type 为 'assets' 时, 需要判断是 file 还是 dir. (其他情
                况均是 dir, 不用判断)
        srcdir
    
    Yields:
        dirpath
    """
    
    def copy_tree_excludes_protected_folders(rootdir_i, rootdir_o):
        invalid_pattern = re.compile(r'/(\.|__?)\w+')
        #   e.g. '/.git', '/__pycache__'
        
        valid_dirs = [(rootdir_i, rootdir_o)]  # [(i, o), ...]
        # FIXME: 1.4.4 版本的 lk-utils.filesniff.findall_dirs 不完善, 无法完全地
        #   过滤掉需要被排除的文件, 所以我们自定义一个 invalid_pattern 来处理
        for dir_i in filesniff.findall_dirs(rootdir_i):
            if invalid_pattern.search(dir_i):
                continue
            dir_o = f'{rootdir_o}/{dir_i.replace(rootdir_i + "/", "", 1)}'
            valid_dirs.append((dir_i, dir_o))
        lk.reset_count()
        
        for (i, o) in valid_dirs:
            filesniff.force_create_dirpath(o)
            for fp, fn in filesniff.find_files(i, fmt='zip'):
                fp_i, fp_o = fp, f'{o}/{fn}'
                shutil.copyfile(fp_i, fp_o)
        del valid_dirs
    
    for dir_i, type_ in attachments.items():
        dirname = ospath.basename(dir_i)
        dir_o = f'{srcdir}/{dirname}'
        
        ''' pyswitch
        from pyswitch import switch
        
        switch(lambda v: bool(v in type_), """
            case 'assets':
                pass
            case 'root_folder':
                pass
            ...
        """)
        '''
        
        # FIXME: 目前的 type_ 设计得不是很好, 比如 type_ = 'root_assets', 会导致
        #   `'root_assets' in type_` 和 `'assets' in type_` 都判断为 True, 所以
        #   不得不指定判断的先后顺序来解决这个歧义.
        #   要消除这个歧义并不难, 我们已经通过指定判断的先后顺序解决了此歧义 (尽
        #   管这不够优雅); 我们仍在评估将 type_ 拆分为元组的必要性, 目前持观望态
        #   度.
        # # type_ = tuple(type_.split(','))
        
        if 'root_assets' in type_:
            if not ospath.exists(dir_o): os.mkdir(dir_o)
            for fp, fn in filesniff.find_files(dir_i, fmt='zip'):
                shutil.copyfile(fp, f'{dir_o}/{fn}')
            # for dp, dn in filesniff.find_dirs(dir_i, fmt='zip'):
            #     os.mkdir(f'{dir_o}/{dn}')
        elif 'assets' in type_:
            if 'compile' in type_:
                copy_tree_excludes_protected_folders(dir_i, dir_o)
            elif ospath.isfile(dir_i):
                file_i, file_o = dir_i, dir_o
                shutil.copyfile(file_i, file_o)
            else:
                filesniff.force_create_dirpath(ospath.dirname(dir_o))
                shutil.copytree(dir_i, dir_o)
        elif 'only_folders' in type_:
            if not ospath.exists(dir_o): os.mkdir(dir_o)
            for dp, dn in filesniff.findall_dirs(dir_i, fmt='zip'):
                os.mkdir(dp.replace(dir_i, dir_o, 1))
        elif 'only_folder' in type_:
            os.mkdir(dir_o)
        
        if 'compile' in type_:
            yield dir_o


def _copy_checkup_tool(buildir):
    dir_i, dir_o = 'checkup', buildir
    try:
        shutil.copyfile(f'{dir_i}/doctor.py', f'{dir_o}/doctor.py')
        shutil.copyfile(f'{dir_i}/pretty_print.py', f'{dir_o}/pretty_print.py')
    except FileNotFoundError:
        shutil.copyfile(f'{dir_i}/doctor.pyc', f'{dir_o}/doctor.pyc')
        shutil.copyfile(f'{dir_i}/pretty_print.pyc', f'{dir_o}/pretty_print.pyc')


def _create_launcher(app_name, icon, target, rootdir, pyversion,
                     extend_sys_paths=None, enable_venv=True,
                     enable_console=True):
    """ 创建启动器.
    
    Args:
        app_name (str)
        icon (str)
        target (dict): {
            'file': filepath,
            'function': str,
            'args': [...],
            'kwargs': {...}
        }
        rootdir (str): 打包的根目录
        pyversion (str)
        extend_sys_paths (list):. 模块搜索路径, 该路径会被添加到 sys.path.
            列表中的元素是相对于 srcdir 的文件夹路径 (必须是相对路径格式. 参考
            `process_pyproject:conf_o['build']['module_paths']`)
        enable_venv (bool): 推荐为 True
    
    详细说明:
        启动器分为两部分, 一个是启动器图标, 一个引导装载程序.
        启动器图标位于: '{rootdir}/{app_name}.exe'
        引导装载程序位于: '{rootdir}/src/bootloader.pyc'
        
        1. 二者的体积都非常小
        2. 启动器本质上是一个带有自定义图标的 bat 脚本. 它指定了 Python 编译器的
           路径和 bootloader 的路径, 通过调用编译器执行 bootloader.pyc
        3. bootloader 主要完成了以下两项工作:
            1. 向 sys.path 中添加当前工作目录和自定义的模块目录
            2. 对主脚本加了一个 try catch 结构, 以便于捕捉主程序报错时的信息, 并
               以系统弹窗的形式给用户. 这样就摆脱了控制台打印的需要, 使我们的软
               件表现得更像是一款软件
    
    Notes:
        1. 启动器在调用主脚本 (main:args:main_script) 之前, 会通过 `os.chdir` 切
           换到主脚本所在的目录, 这样我们项目源代码中的所有相对路径, 相对引用都
           能正常工作
    
    References:
        template/launch_by_system.bat
        template/launch_by_venv.bat
        template/bootloader.txt
    """
    launcher_name = app_name
    bootloader_name = 'bootloader'
    
    target_path = target['file']  # type: str
    target_dir = target_path.rsplit('/', 1)[0]
    target_pkg = target_dir.replace('/', '.')
    target_name = filesniff.get_filename(target_path, suffix=False)
    
    template = loads('template/bootloader.txt')
    code = template.format(
        # see `template/bootloader.txt:Template placeholders`
        SITE_PACKAGES='../venv/site-packages' if enable_venv else '',
        EXTEND_SYS_PATHS=str(extend_sys_paths),
        TARGET_PATH=target_path,
        TARGET_DIR=target_dir,
        TARGET_PKG=target_pkg,
        TARGET_NAME=target_name,
        TARGET_FUNC=target['function'],
        TARGET_ARGS=str(target['args']),
        TARGET_KWARGS=str(target['kwargs']),
    )
    dumps(code, f'{rootdir}/src/{bootloader_name}.py')
    #   这里生成的是 .py 文件. 我们会在 `_apply_config` 的后期做编译工作, 届时会
    #   将它转换为 .pyc, 并删除 .py 文件. (见 `_apply_config:#:compile '{srcdir}
    #   /bootloader.py'`)
    
    # --------------------------------------------------------------------------
    
    if not GlobalConf.create_launcher:
        return
    
    if enable_venv:  # suggest
        template = loads('template/launch_by_venv.bat')
    else:
        # 注意: 这个不太安全, 因为我们不能确定用户系统安装默认的 Python 版本是否
        # 与当前编译的 pyc 版本相同.
        template = loads('template/launch_by_system.bat')
    code = template.format(
        PYVERSION=pyversion.replace('.', ''),  # ...|'37'|'38'|'39'|...
        LAUNCHER=f'{bootloader_name}.pyc'
        #   注意是 '{boot_name}.pyc' 而不是 '{boot_name}.cpython-38.pyc', 原因见
        #   `_compile_py_files:vars:ofp:comment:#2`
    )
    bat_file = f'{rootdir}/{launcher_name}.bat'
    dumps(code, bat_file)
    
    # 这是一个耗时操作 (大约需要 10s), 我们把它放在子线程执行
    def generate_exe(bat_file, exe_file, icon_file, *options):
        from .bat_2_exe import bat_2_exe
        lk.loga('converting bat to exe... '
                'it may take several seconds ~ one minute...')
        bat_2_exe(bat_file, exe_file, icon_file, *options)
        lk.loga('convertion bat-to-exe done')
        os.remove(bat_file)
    
    thread = Thread(
        target=generate_exe,
        args=(bat_file, f'{rootdir}/{launcher_name}.exe', icon,
              '/x64', '' if enable_console else '/invisible')
    )
    thread.start()
    # thread.join()


def _copy_venv(src_venv_dir, dst_venv_dir, pyversion):
    """
    Args:
        src_venv_dir: 'source virtual environment directory'.
            tip: you can pass an empty to this argument, see reason at `Notes:3`
        dst_venv_dir: 'distributed virtual environment directory'
        pyversion: e.g. '3.8'. 请确保该版本与 pyportable_installer 所用的 Python
            编译器, 以及 src_venv_dir 所用的 Python 版本一致 (修订号可以不一样),
            否则 _compile_py_files 编译出来的 .pyc 文件无法运行!

    Notes:
        1. 本函数使用了 embed_python 独立安装包的内容, 而非简单地把 src_venv_dir
           拷贝到打包目录, 这是因为 src_venv_dir 里面的 Python 是不可独立使用的.
           也就是说, 在一个没有安装过 Python 的用户电脑上, 调用 src_venv_dir 下
           的 Python 编译器将失败! 所以我们需要的是一个嵌入版的 Python (在
           Python 官网下载带有 "embed" 字样的压缩包, 并解压, 我在 pyportable
           _installer 项目下已经准备了一份)
        2. 出于性能和成本考虑, 您不必提供有效 src_venv_dir 参数, 即您可以给该参
           数传入一个空字符串, 这样本函数会仅创建虚拟环境的框架 (dst_venv_dir),
           并让 '{dst_venv_dir}/site-packages' 留空. 稍后您可以手动地复制, 或剪
           切所需的依赖到 '{dst_venv_dir}/site-packages'

    Results:
        copy source dir to target dir:
            lib/python-{version}-embed-amd64 -> {dst_venv_dir}
            {src_venv_dir}/Lib/site-packages -> {dst_venv_dir}/site-packages
    """
    # create venv shell
    conf = loads('../embed_python/conf.json')
    embed_python_dir = {
        # see: 'embed_python/README.md'
        '3.8': f'../embed_python/{conf["PY38"]}',
        '3.9': f'../embed_python/{conf["PY39"]}'
    }[pyversion]
    
    shutil.copytree(embed_python_dir, dst_venv_dir)
    
    # copy site-packages
    if ospath.exists(src_venv_dir):
        shutil.copytree(f'{src_venv_dir}/Lib/site-packages',
                        f'{dst_venv_dir}/site-packages')
    else:  # just create an empty folder
        os.mkdir(f'{dst_venv_dir}/site-packages')


def _create_readme(file_i: str, distdir):
    file_o = f'{distdir}/{ospath.basename(file_i)}'
    shutil.copyfile(file_i, file_o)


def _compile_py_files(dir_i, recursive=True):
    """
    References:
        https://blog.csdn.net/weixin_38314865/article/details/90443135
    """
    compile_dir(dir_i, maxlevels=10 if recursive else 0, quiet=1, legacy=True)
    #   maxlevels: int. 指定遍历的深度, 最小为 0 (0 只编译当前目录下的 py 文件).
    #       注意该值在 Python 3.8 下默认是 10, 在 Python 3.9 下默认是 None. 为了
    #       能够在 3.8 及以下正常工作, 所以我用了 10.
    #   quiet: 1 表示只在有错误时向控制台打印信息.
    #   legacy: True 令生成的 pyc 文件位于与 py 的同一目录下, 并且后缀为 '.pyc';
    #       False 令生成的 pyc 文件位于 py 同目录下的 '__pycache__' 目录, 并且后
    #       缀为 '.cpython-xx.pyc'


def _cleanup_py_files(dir_i, recursive=True):
    if recursive:
        # delete __pycache__ folders (the folders are empty)
        for dp in filesniff.findall_dirs(  # dp: 'dirpath'
                dir_i, suffix='__pycache__',
                exclude_protected_folders=False
        ):
            os.rmdir(dp)
        
        # and delete .py files
        for fp in filesniff.findall_files(dir_i, suffix='.py'):
            os.remove(fp)
    else:
        if ospath.exists(d := f'{dir_i}/__pycache__'):
            os.rmdir(d)
        for fp in filesniff.find_files(dir_i, suffix='.py'):
            os.remove(fp)
