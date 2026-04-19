function outFile = vna_make_9ch_compatible(inFile, outFile, maxChan)
%VNA_MAKE_9CH_COMPATIBLE Create a channel-limited VNA MAT-file copy.
%   outFile = vna_make_9ch_compatible(inFile)
%   outFile = vna_make_9ch_compatible(inFile, outFile)
%   outFile = vna_make_9ch_compatible(inFile, outFile, maxChan)
%
% Default maxChan is 9.

if nargin < 1 || isempty(inFile)
    error('Input file is required.');
end
if nargin < 3 || isempty(maxChan)
    maxChan = 9;
end
if nargin < 2 || isempty(outFile)
    [p, n, e] = fileparts(inFile);
    outFile = fullfile(p, [n, '_', num2str(maxChan), 'ch', e]);
end

valid = 1:maxChan;
data = load(inFile, '-mat');

% Prefer SLm, then fallback to other struct-like containers.
if isfield(data, 'SLm') && isstruct(data.SLm)
    data.SLm = trim_slm(data.SLm, valid);
else
    vars = fieldnames(data);
    for k = 1:numel(vars)
        v = data.(vars{k});
        if isstruct(v) && has_clist_like_fields(v)
            data.(vars{k}) = trim_slm(v, valid);
        end
    end
end

% Plot state from file can also carry channel indices.
if isfield(data, 'xplot_s1')
    data.xplot_s1 = trim_xplot_state(data.xplot_s1, maxChan);
end

% Keep channel metadata aligned when present.
if isfield(data, 'ChanStat') && isnumeric(data.ChanStat) && size(data.ChanStat, 1) > maxChan
    data.ChanStat = data.ChanStat(1:maxChan, :);
end
if isfield(data, 'ChanLabel') && size(data.ChanLabel, 1) > maxChan
    data.ChanLabel = data.ChanLabel(1:maxChan, :);
end
if isfield(data, 'EULabel') && size(data.EULabel, 1) > maxChan
    data.EULabel = data.EULabel(1:maxChan, :);
end

save(outFile, '-struct', 'data', '-mat');
fprintf('Saved 9ch-compatible file: %s\n', outFile);

end

function tf = has_clist_like_fields(s)
tf = isfield(s, 'clist') || isfield(s, 'xcstate') || isfield(s, 'scmeas');
end

function s = trim_slm(s, valid)
if isfield(s, 'clist')
    s.clist = intersect(rowvec(s.clist), valid, 'stable');
end
if isfield(s, 'numin')
    s.numin = min(double(s.numin), max(valid));
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
        resp = xc.resp;
        for i = 1:numel(resp)
            if isfield(resp(i), 'r')
                resp(i).r = intersect(rowvec(resp(i).r), valid, 'stable');
            end
        end
        xc.resp = resp;
    end
    s.xcstate = xc;
end

if isfield(s, 'scmeas') && isstruct(s.scmeas) && numel(s.scmeas) > max(valid)
    s.scmeas = s.scmeas(1:max(valid));
end

if isfield(s, 'xcmeas') && isstruct(s.xcmeas)
    sz = size(s.xcmeas);
    if numel(sz) == 2
        s.xcmeas = s.xcmeas(1:min(sz(1), max(valid)), 1:min(sz(2), max(valid)));
    elseif numel(sz) == 1
        s.xcmeas = s.xcmeas(1:min(sz(1), max(valid)));
    end
end
end

function x = trim_xplot_state(x, maxChan)
if ~isstruct(x)
    return;
end

for i = 1:numel(x)
    if isfield(x(i), 'ylcb') && isnumeric(x(i).ylcb)
        y = rowvec(x(i).ylcb);
        x(i).ylcb = y(1:min(numel(y), maxChan));
    end
    if isfield(x(i), 'xc_cmax') && ~isempty(x(i).xc_cmax) && isnumeric(x(i).xc_cmax)
        x(i).xc_cmax = min(double(x(i).xc_cmax), maxChan);
    end
    if isfield(x(i), 'xchanv') && isstruct(x(i).xchanv) && isfield(x(i).xchanv, 'xc_ckstate')
        m = x(i).xchanv.xc_ckstate;
        if isnumeric(m) && ~isempty(m) && size(m, 2) > maxChan
            m(:, maxChan+1:end) = 0;
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
