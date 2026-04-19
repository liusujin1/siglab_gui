function varargout = plot_vna(varargin)
%PLOT_VNA Compatibility shim for legacy plot_vna.p index-overflow on load.
%
% This shim must be earlier on MATLAB path than legacy plot_vna.p.
% It delegates all actions to legacy plot_vna.p, but guards 'load' calls
% so startup can continue if legacy saved state contains out-of-range
% channel indices for current hardware limits.

if nargin == 0
    [varargout{1:nargout}] = call_legacy_plot_vna();
    return;
end

action = varargin{1};
if ~(ischar(action) || (isstring(action) && isscalar(action)))
    [varargout{1:nargout}] = call_legacy_plot_vna(varargin{:});
    return;
end

action = char(action);
if strcmpi(action, 'init')
    args = sanitize_init_args(varargin);
    [varargout{1:nargout}] = call_legacy_plot_vna(args{:});
    return;
end

if strcmpi(action, 'load')
    args = sanitize_load_args(varargin);
    try
        [varargout{1:nargout}] = call_legacy_plot_vna(args{:});
    catch ME
        % Known legacy failure: channel index in loaded state > #line handles.
        if is_index_overflow(ME)
            warning('plot_vna:load_state_ignored', ...
                ['Ignoring incompatible saved plot state due to legacy index overflow. ', ...
                 'Continuing with initialized default plot state.']);
            varargout = cell(1, nargout);
            return;
        end
        rethrow(ME);
    end
    return;
end

[varargout{1:nargout}] = call_legacy_plot_vna(varargin{:});
end

function tf = is_index_overflow(ME)
msg = string(ME.message);
id = string(ME.identifier);
tf = contains(msg, "Index exceeds") || ...
     contains(msg, "Index must not exceed") || ...
     contains(id, "badsubscript") || ...
     contains(id, "IndexExceeds");
end

function args = sanitize_init_args(args)
% args layout for 'init' in this codebase:
% 1 action, 2 [Numin, NCperBox], 3 colors, 4 owner figure handle
if numel(args) >= 2 && isnumeric(args{2}) && numel(args{2}) >= 1
    v = args{2};
    v(1) = min(double(v(1)), 9);
    args{2} = v;
end
end

function args = sanitize_load_args(args)
% args layout for 'load' in this codebase:
% 1 action, 2 xplot_s1, 3 xplot_s2, 4 xplot_axes, 5 grids, 6 SLm/new or []/old

if numel(args) < 2
    return;
end

% Trim xplot_s1 per-axis channel enable vectors and ckstate.
if numel(args) >= 2 && isstruct(args{2})
    args{2} = trim_xplot_s1(args{2}, 9);
end

% Trim SLm if present (new format path).
if numel(args) >= 6 && isstruct(args{6})
    args{6} = trim_slm(args{6}, 9);
end
end

function s = trim_slm(s, maxChan)
valid = 1:maxChan;
if isfield(s, 'clist')
    s.clist = intersect(rowvec(s.clist), valid, 'stable');
end
if isfield(s, 'numin')
    s.numin = min(double(s.numin), maxChan);
end
if isfield(s, 'xcstate') && isstruct(s.xcstate)
    xc = s.xcstate;
    if isfield(xc, 'clist')
        xc.clist = intersect(rowvec(xc.clist), valid, 'stable');
    end
    if isfield(xc, 'refc')
        xc.refc = intersect(rowvec(xc.refc), valid, 'stable');
    end
    if isfield(xc, 'resp')
        for i = 1:numel(xc.resp)
            if isfield(xc.resp(i), 'r')
                xc.resp(i).r = intersect(rowvec(xc.resp(i).r), valid, 'stable');
            end
        end
    end
    s.xcstate = xc;
end
if isfield(s, 'scmeas') && isstruct(s.scmeas) && numel(s.scmeas) > maxChan
    s.scmeas = s.scmeas(1:maxChan);
end
if isfield(s, 'xcmeas') && isstruct(s.xcmeas)
    sz = size(s.xcmeas);
    if numel(sz) == 2
        s.xcmeas = s.xcmeas(1:min(sz(1),maxChan), 1:min(sz(2),maxChan));
    end
end
end

function x = trim_xplot_s1(x, maxChan)
for i = 1:numel(x)
    if isfield(x(i), 'ylcb') && isnumeric(x(i).ylcb)
        y = rowvec(x(i).ylcb);
        x(i).ylcb = y(1:min(numel(y), maxChan));
    end
    if isfield(x(i), 'xc_cmax') && isnumeric(x(i).xc_cmax) && ~isempty(x(i).xc_cmax)
        x(i).xc_cmax = min(double(x(i).xc_cmax), maxChan);
    end
    if isfield(x(i), 'xchanv') && isstruct(x(i).xchanv) && isfield(x(i).xchanv, 'xc_ckstate')
        m = x(i).xchanv.xc_ckstate;
        if isnumeric(m) && ~isempty(m)
            if size(m,2) > maxChan
                m(:, maxChan+1:end) = 0;
            end
            x(i).xchanv.xc_ckstate = m;
        end
    end
end
end

function v = rowvec(v)
if isempty(v)
    v = [];
elseif iscolumn(v)
    v = v.';
end
end

function varargout = call_legacy_plot_vna(varargin)
shimDir = fileparts(mfilename('fullpath'));
pathWithGuards = [pathsep, path, pathsep];
wasOnPath = contains(pathWithGuards, [pathsep, shimDir, pathsep]);
if wasOnPath
    rmpath(shimDir);
end
c = onCleanup(@()restore_path(shimDir, wasOnPath)); %#ok<NASGU>
[varargout{1:nargout}] = feval('plot_vna', varargin{:});
end

function restore_path(shimDir, wasOnPath)
if wasOnPath
    addpath(shimDir, '-begin');
end
end
