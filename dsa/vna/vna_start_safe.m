function vna_start_safe()
%VNA_START_SAFE Start vna with safe path ordering and default file.

baseDir = fileparts(mfilename('fullpath'));
rootDir = fileparts(baseDir); % .../dsa
vcomDir = fullfile(rootDir, 'vcom');
compatDir = fullfile(baseDir, 'compat');
srcDefault = fullfile(baseDir, 'default.vna');
origDefault = fullfile(baseDir, 'default_original.vna');
legacyRoot = fullfile(getenv('USERPROFILE'), 'Documents', 'MATLAB', 'dsa');
legacyVnaDir = fullfile(legacyRoot, 'vna');
legacyVcomDir = fullfile(legacyRoot, 'vcom');

% Remove legacy shadow paths first.
try
    rmpath(legacyVnaDir);
catch
end
try
    rmpath(legacyVcomDir);
catch
end

if exist(vcomDir, 'dir') == 7
    addpath(vcomDir, '-begin');
end
% Keep vna directory on path, but make compat shim highest priority so
% plot_vna load-time fixes are applied before legacy implementation.
addpath(baseDir, '-begin');
if exist(compatDir, 'dir') == 7
    addpath(compatDir, '-begin');
end

rehash;

disp('--- VNA path diagnostic ---');
disp(which('vna', '-all'));
disp(which('plot_vna', '-all'));
disp(which('ls_vna', '-all'));
disp(which('siglab', '-all'));
disp(which('hw_stat', '-all'));
disp('---------------------------');

requiredFns = {'hw_stat','ls_vna','pathfind','virun','v_dlg1','plot_vna'};
for i = 1:numel(requiredFns)
    if isempty(which(requiredFns{i}))
        error('Missing required function on MATLAB path: %s', requiredFns{i});
    end
end

% Clear stale hardware ownership from previous abnormal/forced shutdowns.
try
    hw_stat('clear');
catch
end

if exist(srcDefault, 'file') ~= 2
    error('default.vna not found: %s', srcDefault);
end

if exist(origDefault, 'file') == 2
    startFile = 'default_original.vna';
else
    startFile = 'default.vna';
end

if exist(fullfile(baseDir,'vna_core.p'),'file') == 2 || exist('vna_core','file') == 2
    vna_core('init', [baseDir, filesep], startFile);
else
    error(['vna_core is missing. Run vna_enable_shortcut once after closing MATLAB, ', ...
           'then reopen and use vna.']);
end

% Install shutdown-safe callbacks to bypass legacy close-time callback chain.
vna_install_safe_close();
end

function vna_install_safe_close()
mainFig = findobj('type','figure','tag','vna_fig');
if ~isempty(mainFig)
    try
        set(mainFig(1), 'CloseRequestFcn', 'vna_safe_close');
    catch
    end
    try
        hMenus = findall(mainFig(1), 'Type', 'uimenu');
        for k = 1:numel(hMenus)
            try
                lbl = get(hMenus(k), 'Label');
                if ischar(lbl) && ~isempty(strfind(lbl,'Quit')) %#ok<STREMP>
                    set(hMenus(k), 'Callback', 'vna_safe_close');
                end
            catch
            end
        end
    catch
    end
end

plotFig = findobj('type','figure','tag','vna_plot');
if ~isempty(plotFig)
    try
        set(plotFig(1), 'CloseRequestFcn', 'vna_safe_close');
    catch
    end
end
end
