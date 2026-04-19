function varargout = vna(varargin)
%VNA Wrapper entrypoint.
% - vna() routes to vna_start_safe
% - vna(action,...) forwards to legacy vna_core when installed

baseDir = fileparts(mfilename('fullpath'));
rootDir = fileparts(baseDir);
vcomDir = fullfile(rootDir, 'vcom');
compatDir = fullfile(baseDir,'compat');
coreP = fullfile(baseDir,'vna_core.p');
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

if exist(vcomDir,'dir') == 7
    addpath(vcomDir,'-begin');
end
if exist(compatDir,'dir') == 7
    addpath(compatDir,'-begin');
end

if nargin == 0
    [varargout{1:nargout}] = vna_start_safe();
    return;
end

% Ensure the folder containing vna_core.p is on path before dispatch.
addpath(baseDir,'-begin');
rehash;

if exist(coreP,'file') ~= 2 && exist('vna_core','file') ~= 2
    error(['vna_core is not installed yet. Run vna_enable_shortcut after ', ...
           'closing all MATLAB sessions.']);
end

[varargout{1:nargout}] = vna_core(varargin{:});
end
