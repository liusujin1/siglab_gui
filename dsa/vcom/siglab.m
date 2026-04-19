function varargout = siglab(action,varargin)
%SIGLAB Hybrid gateway:
% 1) Try dispatching to a real backend (if one exists on MATLAB path).
% 2) Fallback to offline-compatible behavior to avoid hard crashes.

persistent NEXT_REQ REQ_DB INP_GAIN NI_STATE
if isempty(NEXT_REQ)
    NEXT_REQ = 0;
    REQ_DB = struct('id', {}, 'npts', {}, 'chans', {}, 'kind', {}, 'ref', {}, 'navg', {}, 'seq', {}, 'data', {}, 'err', {});
    INP_GAIN = ones(1, 64);
    NI_STATE = init_ni_state();
end

if nargin < 1 || isempty(action)
    error('No specified command!');
end

% Try real backend first (avoid self-shadowing).
[ok, out] = try_real_backend(action, varargin, nargout);
if ok
    varargout = out;
    return;
end

% Refresh NI selection if user changed globals at runtime.
NI_STATE = sync_ni_state(NI_STATE);

% Fallback compatibility path.
cmd = lower(char(action));
switch cmd
    case 'ioinit'
        if NI_STATE.available
            nin = max(1, NI_STATE.maxInputs);
            nout = 0;
            bw = max(1, floor(NI_STATE.rate / 2.56));
            varargout{1} = nin;
            if nargout >= 2, varargout{2} = nout; end
            if nargout >= 3, varargout{3} = bw; end
            if nargout >= 4, varargout{4} = 'NI-DAQmx'; end
            return;
        end
        varargout{1} = 4;
        if nargout >= 2, varargout{2} = 1; end
        if nargout >= 3, varargout{3} = 20000; end
        if nargout >= 4, varargout{4} = '01\23\01 19:16 '; end

    case 'get'
        key = '';
        if ~isempty(varargin), key = lower(char(varargin{1})); end
        switch key
            case 'bias'
                varargout{1} = 1;
            case 'inpgain'
                ch = 1;
                if numel(varargin) >= 2 && ~isempty(varargin{2}), ch = varargin{2}; end
                ch = max(1, min(numel(INP_GAIN), double(ch(1))));
                varargout{1} = INP_GAIN(ch);
            case 'ni_state'
                varargout{1} = NI_STATE;
            otherwise
                varargout{1} = [];
        end

    case 'inpgain'
        if numel(varargin) >= 2
            chans = double(varargin{1});
            gainv = double(varargin{2});
            if isempty(chans), chans = 1; end
            if numel(gainv) == 1, gainv = gainv * ones(size(chans)); end
            for k = 1:min(numel(chans), numel(gainv))
                ch = max(1, min(numel(INP_GAIN), chans(k)));
                INP_GAIN(ch) = gainv(k);
            end
            NI_STATE = ni_apply_inpgain(NI_STATE, chans, varargin);
        end
        if nargout > 0, varargout{1} = 0; end

    case {'outlevel','inpset','trigger','setwindow','process','event','outburst', ...
          'outsine','sendcal','sendarb','compute','rawcommand','playback','exe', ...
          'misc'}
        if strcmp(cmd, 'inpset') && NI_STATE.available
            [NI_STATE, INP_GAIN] = ni_apply_inpset(NI_STATE, INP_GAIN, varargin);
            NI_STATE = ni_sync_mc_params(NI_STATE);
        end
        if strcmp(cmd, 'trigger') && nargout >= 1
            varargout{1} = 0;
        elseif nargout > 0
            varargout{1} = 0;
        end

    case 'delayms'
        if ~isempty(varargin)
            pause(max(0, double(varargin{1})) / 1000);
        end
        if nargout > 0, varargout{1} = 0; end

    case 'datareq'
        NEXT_REQ = NEXT_REQ + 1;
        req.id = NEXT_REQ;
        req.npts = max(1, round(double(varargin{1})));
        req.chans = varargin{2};
        req.kind = '';
        req.ref = [];
        req.navg = 0;
        req.seq = 0;
        req.data = [];
        req.err = '';
        for i = 3:numel(varargin)
            if ischar(varargin{i}) || (isstring(varargin{i}) && isscalar(varargin{i}))
                s = char(varargin{i});
                sl = lower(s);
                if any(strcmp(sl, {'timea','timei','aspeca','aspeci','fft','acor','xfer','coh','cspec','ccor','impulse'}))
                    req.kind = s;
                elseif strcmp(sl, 'ref') && i < numel(varargin)
                    req.ref = varargin{i+1};
                end
            end
        end
        if NI_STATE.available
            try
                [req.data, NI_STATE] = ni_acquire_block(NI_STATE, INP_GAIN, req.npts, req.chans, req.kind, req.ref);
                req.navg = 1;
                req.seq = 1;
                req.err = '';
                NI_STATE.lastError = '';
            catch
                req.data = [];
                % Do not block DataRdy forever; let DataGet attempt one more
                % acquisition and surface the real error if still failing.
                req.navg = 1;
                req.seq = 0;
                req.err = 'NI acquisition failed in DataReq';
                NI_STATE.lastError = req.err;
            end
        end
        REQ_DB(end+1) = req;
        varargout{1} = req.id;

    case 'datardy'
        req = find_req(REQ_DB, varargin{1});
        if isempty(req)
            varargout{1} = -1;
        else
            idx = req.idx;
            if NI_STATE.available
                if REQ_DB(idx).navg < 1
                    REQ_DB(idx).navg = 1;
                end
            else
                REQ_DB(idx).navg = REQ_DB(idx).navg + 1;
            end
            varargout{1} = REQ_DB(idx).navg;
        end

    case 'dataget'
        req = find_req(REQ_DB, varargin{1});
        if isempty(req)
            if nargout >= 1, varargout{1} = []; end
            if nargout >= 2, varargout{2} = 0; end
            if nargout >= 3, varargout{3} = 0; end
            return;
        end
        idx = req.idx;
        r = REQ_DB(idx);
        npts = r.npts;
        chans = double(r.chans(:)');
        if isempty(chans), chans = 1; end
        nchan = numel(chans);
        if NI_STATE.available
            if isempty(r.data)
                try
                    [data, NI_STATE] = ni_acquire_block(NI_STATE, INP_GAIN, npts, chans, r.kind, r.ref);
                    REQ_DB(idx).data = data;
                    REQ_DB(idx).navg = 1;
                    REQ_DB(idx).err = '';
                    NI_STATE.lastError = '';
                catch ME
                    REQ_DB(idx).err = ME.message;
                    NI_STATE.lastError = ME.message;
                    error('siglab:NIReadFailed', 'NI acquisition failed: %s', ME.message);
                end
            else
                data = r.data;
            end
            REQ_DB(idx).seq = REQ_DB(idx).seq + 1;
            if nargout >= 1, varargout{1} = data; end
            if nargout >= 2, varargout{2} = 0; end
            if nargout >= 3, varargout{3} = REQ_DB(idx).seq; end
            return;
        end
        t = (0:npts-1)' / max(1, npts-1);
        data = zeros(npts, nchan);
        kind = lower(r.kind);
        for k = 1:nchan
            ph = 2*pi*(0.09*k + 0.02*max(0, chans(k)-1));
            switch kind
                case {'timea','timei'}
                    data(:,k) = sin(2*pi*(2+0.3*k)*t + ph);
                case {'aspeca','aspeci','fft'}
                    data(:,k) = 0.05 + abs(sin(2*pi*(1.5+0.2*k)*t + ph));
                case 'coh'
                    data(:,k) = 0.5 + 0.5*sin(2*pi*(0.8+0.05*k)*t + ph);
                    data(:,k) = max(0, min(1, data(:,k)));
                otherwise
                    data(:,k) = 0.2*sin(2*pi*(1.2+0.1*k)*t + ph);
            end
        end
        REQ_DB(idx).seq = REQ_DB(idx).seq + 1;
        if nargout >= 1, varargout{1} = data; end
        if nargout >= 2, varargout{2} = 0; end
        if nargout >= 3, varargout{3} = REQ_DB(idx).seq; end

    case 'dataabort'
        req = find_req(REQ_DB, varargin{1});
        if ~isempty(req), REQ_DB(req.idx) = []; end
        if nargout > 0, varargout{1} = 0; end

    case 'instatus'
        if nargout > 0, varargout{1} = 0; end

    case 'debug'
        code = [];
        if ~isempty(varargin), code = varargin{1}; end
        switch double(code)
            case -20
                outs = {1001, 4, 1, 0};
            case -24
                outs = {1, '01/23/01', 'PROM 0.0', 0};
            case -25
                outs = {1, '01/23/01', 'CODE 0.0'};
            case -26
                outs = {1, 1, 1};
            case -27
                outs = {1, '01/23/01', 'DLL 0.0'};
            case -37
                outs = {0, 0};
            case -42
                outs = {0};
            case -45
                outs = {0, 0};
            otherwise
                outs = {0, 0, 0, 0};
        end
        for i = 1:nargout
            if i <= numel(outs), varargout{i} = outs{i}; else, varargout{i} = 0; end
        end

    otherwise
        error('No specified command!');
end
end

function st = init_ni_state()
st = struct('available', false, 'device', '', 'rate', 51200, ...
            'enabledChans', 1:4, 'maxInputs', 4, 'model', '', ...
            'frameSize', 1024, 'bw', 20000, 'overlap', 0, ...
            'offset', zeros(1,64), 'range', nan(1,64), ...
            'mode', zeros(1,64), ...
            'runtime', ni_runtime_empty(), ...
            'lastError', '', ...
            'lastInpSet', [], 'lastInpGain', [], ...
            'lastApply', struct('logicalChans', [], 'physicalAi', [], ...
                                'measurementType', '', 'iepeCurrent', NaN, ...
                                'configuredRange', [], 'appliedRange', [], ...
                                'appliedCoupling', {{}}, ...
                                'timestamp', ''));
global NI_SIGLAB_DEVICE NI_SIGLAB_MAXINPUTS
try
    if ~(exist('daq', 'file') == 2 && exist('daqlist', 'file') == 2)
        return;
    end
    t = daqlist("ni");
    if isempty(t)
        return;
    end
    row = 1;
    if ~isempty(NI_SIGLAB_DEVICE)
        devWanted = char(string(NI_SIGLAB_DEVICE));
        v = t.Properties.VariableNames;
        if any(strcmpi(v, 'DeviceID'))
            ids = string(t.DeviceID);
            k = find(ids == string(devWanted), 1, 'first');
            if ~isempty(k), row = k; end
        end
    end
    v = t.Properties.VariableNames;
    if any(strcmpi(v, 'DeviceID'))
        dev = t.DeviceID(row);
    elseif any(strcmpi(v, 'ID'))
        dev = t.ID(row);
    else
        dev = t{row,1};
    end
    if any(strcmpi(v, 'Model'))
        mdl = char(string(t.Model(row)));
    else
        mdl = '';
    end
    st.device = char(string(dev));
    st.model = mdl;
    st.available = ~isempty(st.device);
    if ~isempty(NI_SIGLAB_MAXINPUTS)
        st.maxInputs = max(1, round(double(NI_SIGLAB_MAXINPUTS)));
    elseif contains(upper(mdl), 'USB-4431')
        st.maxInputs = 4;
    elseif contains(upper(mdl), 'USB-6000')
        st.maxInputs = 8;
    else
        st.maxInputs = 4;
    end
catch
    st.available = false;
end
end

function st = sync_ni_state(st)
global NI_SIGLAB_DEVICE NI_SIGLAB_MAXINPUTS
if ~st.available
    st = init_ni_state();
    return;
end
changed = false;
if ~isempty(NI_SIGLAB_DEVICE)
    devWanted = char(string(NI_SIGLAB_DEVICE));
    if ~strcmpi(devWanted, st.device)
        changed = true;
    end
end
if ~isempty(NI_SIGLAB_MAXINPUTS)
    nin = max(1, round(double(NI_SIGLAB_MAXINPUTS)));
    if nin ~= st.maxInputs
        changed = true;
    end
end
if changed
    st = ni_runtime_reset(st, true);
    st = init_ni_state();
end
end

function [st, inpg] = ni_apply_inpset(st, inpg, args)
global NI_SIGLAB_ACTIVE_CHANS
oldEnabled = st.enabledChans;
oldFrame = st.frameSize;
oldRate = st.rate;
oldBw = st.bw;
oldOverlap = st.overlap;
st.lastInpSet = args;
if numel(args) >= 1 && ~isempty(args{1})
    ch = unique(double(args{1}(:)'));
    ch = ch(ch >= 1);
    if ~isempty(ch)
        st.enabledChans = ch;
    end
end
if ~isempty(NI_SIGLAB_ACTIVE_CHANS)
    chf = unique(round(double(NI_SIGLAB_ACTIVE_CHANS(:)')));
    chf = chf(chf >= 1);
    if ~isempty(chf)
        st.enabledChans = chf;
    end
end
if numel(args) >= 2 && isnumeric(args{2}) && ~isempty(args{2})
    st.frameSize = max(1, round(double(args{2}(1))));
end
for i = 3:numel(args)-1
    if ischar(args{i}) || (isstring(args{i}) && isscalar(args{i}))
        key = lower(char(args{i}));
        if strcmp(key, 'sclock')
            rv = double(args{i+1});
            if isfinite(rv) && rv > 1
                st.rate = rv;
            end
        elseif strcmp(key, 'bw')
            bv = double(args{i+1});
            if isfinite(bv) && bv > 1
                st.bw = bv;
                if ~any(strcmpi(lower(string(args(3:2:end))), 'sclock'))
                    st.rate = max(1, round(2.56 * bv));
                end
            end
        elseif strcmp(key, 'overlap')
            ov = double(args{i+1});
            if isfinite(ov)
                st.overlap = ov;
            end
        end
    end
end
if isempty(st.enabledChans)
    st.enabledChans = 1:4;
end
if numel(inpg) < max(st.enabledChans)
    inpg(max(st.enabledChans)) = 1;
end
changed = ~isequal(double(oldEnabled(:)'), double(st.enabledChans(:)')) || ...
          oldFrame ~= st.frameSize || oldRate ~= st.rate || ...
          oldBw ~= st.bw || oldOverlap ~= st.overlap;
if changed
    st = ni_runtime_reset(st, true);
end
end

function st = ni_sync_mc_params(st)
% Pull per-channel range/offset from legacy MC globals even if InpGain
% callback is skipped by the GUI workflow.
try
    changed = false;
    for ch = st.enabledChans
        csel = max(1, min(numel(st.range), round(ch)));
        [rg, ofs, md] = infer_mc_channel_params(csel);
        if isfinite(rg) && rg > 0
            if ~(isfinite(st.range(csel)) && st.range(csel) == rg), changed = true; end
            st.range(csel) = rg;
        end
        if isfinite(ofs)
            if ~(isfinite(st.offset(csel)) && st.offset(csel) == ofs), changed = true; end
            st.offset(csel) = ofs;
        end
        if isfinite(md)
            if ~(isfinite(st.mode(csel)) && st.mode(csel) == md), changed = true; end
            st.mode(csel) = md;
        end
    end
    if changed
        st = ni_runtime_reset(st, true);
    end
catch
end
end

function st = ni_apply_inpgain(st, chans, args)
st.lastInpGain = args;
chans = double(chans(:)');
if isempty(chans), return; end
gainv = double(args{2});
if numel(gainv) == 1
    gainv = gainv * ones(size(chans));
end
for k = 1:min(numel(chans), numel(gainv))
    ch = max(1, min(numel(st.range), round(chans(k))));
    if isfinite(gainv(k)) && gainv(k) > 0
        st.range(ch) = gainv(k);
    end
end
for i = 3:numel(args)-1
    if ischar(args{i}) || (isstring(args{i}) && isscalar(args{i}))
        if strcmpi(char(args{i}), 'Offset')
            ov = double(args{i+1});
            if numel(ov) == 1
                ov = ov * ones(size(chans));
            end
            for k = 1:min(numel(chans), numel(ov))
                ch = max(1, min(numel(st.offset), round(chans(k))));
                if isfinite(ov(k))
                    st.offset(ch) = ov(k);
                end
            end
        end
    end
end
st = ni_runtime_reset(st, true);
end

function [data, st] = ni_acquire_block(st, inpg, npts, chans, kind, refChan)
global NI_SIGLAB_RESET_ON_RESERVE
global NI_SIGLAB_RUNTIME_REUSE
if ~exist('NI_SIGLAB_RESET_ON_RESERVE','var') || isempty(NI_SIGLAB_RESET_ON_RESERVE)
    NI_SIGLAB_RESET_ON_RESERVE = true;
end
if ~exist('NI_SIGLAB_RUNTIME_REUSE','var') || isempty(NI_SIGLAB_RUNTIME_REUSE)
    NI_SIGLAB_RUNTIME_REUSE = true; % default for better frame-to-frame continuity
end
[data, st] = ni_acquire_block_once(st, inpg, npts, chans, kind, refChan, 0);
if isempty(data)
    data = zeros(max(1, round(double(npts))), max(1, numel(chans)));
end
end

function [data, st] = ni_acquire_block_once(st, inpg, npts, chans, kind, refChan, retryCount)
global NI_SIGLAB_RESET_ON_RESERVE
global NI_SIGLAB_RUNTIME_REUSE
global NI_SIGLAB_ACTIVE_CHANS
if ~exist('NI_SIGLAB_ACTIVE_CHANS','var') || isempty(NI_SIGLAB_ACTIVE_CHANS)
    NI_SIGLAB_ACTIVE_CHANS = [];
end
npts = max(1, round(double(npts)));
reqChans = unique(double(chans(:)'));
if isempty(reqChans)
    reqChans = st.enabledChans;
end
if isempty(reqChans)
    reqChans = 1;
end
reqChans = reqChans(reqChans >= 1);
if isempty(reqChans)
    reqChans = 1;
end
nchReq = numel(reqChans);
acqChans = reqChans;
if ~isempty(NI_SIGLAB_ACTIVE_CHANS)
    forcedCh = unique(round(double(NI_SIGLAB_ACTIVE_CHANS(:)')));
    forcedCh = forcedCh(forcedCh >= 1);
    if ~isempty(forcedCh)
        acqChans = intersect(acqChans, forcedCh, 'stable');
    end
end
% MC setup channel-enable gates actual hardware acquisition, but output
% still preserves requested channel count/order for legacy callers.
if ~isempty(st.enabledChans)
    acqChans = intersect(acqChans, st.enabledChans, 'stable');
end
if isempty(acqChans)
    acqChans = reqChans(1);
end
data = [];
if st.available
    try
        [st, dqobj, readChans, sortIdx, applyInfo] = ni_prepare_runtime(st, inpg, reqChans, acqChans, refChan);
        st.lastApply = applyInfo;
        kindLower = lower(char(kind));
        nsampRead = npts;
        if any(strcmp(kindLower, {'aspeca','aspeci','fft','xfer','coh','cspec'}))
            if isfinite(st.frameSize) && st.frameSize > nsampRead
                nsampRead = max(1, round(double(st.frameSize)));
            end
        end
        useContinuous = NI_SIGLAB_RUNTIME_REUSE && any(strcmp(kindLower, {'timea','timei'}));
        if useContinuous
            try
                if ~isfield(st.runtime,'running') || ~st.runtime.running
                    start(dqobj, "continuous");
                    st.runtime.running = true;
                end
            catch
                st.runtime.running = false;
                useContinuous = false;
            end
        end
        try
            rawSorted = read(dqobj, nsampRead, "OutputFormat", "Matrix");
        catch
            try
                if useContinuous
                    try
                        stop(dqobj);
                    catch
                    end
                    st.runtime.running = false;
                end
                dur = nsampRead / dqobj.Rate;
                rawSorted = read(dqobj, seconds(dur), "OutputFormat", "Matrix");
            catch
                % Rebuild once then retry to avoid stale task states.
                st = ni_runtime_reset(st, true);
                [st, dqobj, readChans, sortIdx, applyInfo] = ni_prepare_runtime(st, inpg, reqChans, acqChans, refChan);
                st.lastApply = applyInfo;
                rawSorted = read(dqobj, nsampRead, "OutputFormat", "Matrix");
            end
        end
        nread = numel(readChans);
        rawRead = zeros(size(rawSorted,1), nread);
        nc = min(size(rawSorted,2), nread);
        rawRead(:, sortIdx(1:nc)) = rawSorted(:,1:nc);
        % Apply configured channel offsets in software (hardware offset control
        % is not uniformly available across NI devices/channels).
        for k = 1:nread
            csel = max(1, min(numel(st.offset), round(readChans(k))));
            ofs = st.offset(csel);
            if isfinite(ofs) && ofs ~= 0
                rawRead(:,k) = rawRead(:,k) - ofs;
            end
        end
        raw = zeros(size(rawRead,1), nchReq);
        for kr = 1:nchReq
            j = find(readChans == reqChans(kr), 1, 'first');
            if ~isempty(j)
                raw(:,kr) = rawRead(:,j);
            end
        end
        rawRef = [];
        if nargin >= 6 && ~isempty(refChan)
            rc = double(refChan(1));
            jref = find(readChans == rc, 1, 'first');
            if ~isempty(jref)
                rawRef = rawRead(:,jref);
            end
        end
        data = adapt_kind(raw(:,1:nchReq), lower(char(kind)), rawRef, npts);
        if ~NI_SIGLAB_RUNTIME_REUSE
            ni_release_daqobj(dqobj);
            st.runtime = ni_runtime_empty();
        end
    catch ME
        msg = string(ME.message);
        if contains(lower(msg), "hardware is reserved")
            st = ni_runtime_reset(st, true);
            if NI_SIGLAB_RESET_ON_RESERVE
                try
                    daqreset;
                catch
                end
            end
            pause(0.15);
            if retryCount < 1
                try
                    [data, st] = ni_acquire_block_once(st, inpg, npts, reqChans, kind, refChan, retryCount + 1);
                    return;
                catch ME2
                    error('siglab:NIReadFailed', 'NI read failed on device %s after auto-retry: %s', st.device, ME2.message);
                end
            end
        end
        error('siglab:NIReadFailed', 'NI read failed on device %s: %s', st.device, ME.message);
    end
end
if isempty(data) && ~st.available
    % Last-resort synthetic data to keep UI alive.
    t = (0:npts-1)' / max(1, npts-1);
    data = zeros(npts, nchReq);
    for k = 1:nchReq
        data(:,k) = sin(2*pi*(2+0.3*k)*t + 2*pi*0.07*k);
    end
    data = adapt_kind(data, lower(char(kind)), [], npts);
end
end

function ni_release_daqobj(dqobj)
try
    if ~isempty(dqobj)
        try
            stop(dqobj);
        catch
        end
        try
            release(dqobj);
        catch
        end
        try
            delete(dqobj);
        catch
        end
    end
catch
end
end

function rt = ni_runtime_empty()
rt = struct('sig', '', 'dqobj', [], 'readChans', [], 'sortIdx', [], ...
            'phys', [], 'configuredRangeRead', [], 'appliedRangeRead', [], ...
            'appliedCouplingRead', {{}}, 'measurementType', '', ...
            'iepeCurrent', NaN, 'running', false);
end

function st = ni_runtime_reset(st, doRelease)
if nargin < 2
    doRelease = false;
end
if ~isfield(st, 'runtime') || isempty(st.runtime)
    st.runtime = ni_runtime_empty();
    return;
end
if doRelease
    dqobj = [];
    if isfield(st.runtime, 'dqobj')
        dqobj = st.runtime.dqobj;
    end
    if ni_obj_valid(dqobj)
        ni_release_daqobj(dqobj);
    end
end
st.runtime = ni_runtime_empty();
end

function tf = ni_obj_valid(obj)
tf = false;
if isempty(obj)
    return;
end
try
    tf = isvalid(obj);
catch
    % Some MATLAB objects may not implement isvalid but still be usable.
    tf = true;
end
end

function [st, dqobj, readChans, sortIdx, applyInfo] = ni_prepare_runtime(st, inpg, reqChans, acqChans, refChan)
global NI_SIGLAB_FORCE_RANGE
global NI_SIGLAB_CHAN_MAP
global NI_SIGLAB_MEAS_TYPE NI_SIGLAB_IEPE_CURRENT
global NI_SIGLAB_FORCE_COUPLING
global NI_SIGLAB_RUNTIME_REUSE

if ~isfield(st, 'runtime') || isempty(st.runtime)
    st.runtime = ni_runtime_empty();
end

measType = "Voltage";
if ~isempty(NI_SIGLAB_MEAS_TYPE)
    measType = string(NI_SIGLAB_MEAS_TYPE);
elseif contains(upper(st.model), "4431")
    measType = "IEPE";
end
iepeI = [];
if ~isempty(NI_SIGLAB_IEPE_CURRENT)
    iepeI = double(NI_SIGLAB_IEPE_CURRENT);
elseif strcmpi(measType, "IEPE")
    iepeI = 0.004;
end

readChans = acqChans;
if nargin >= 5 && ~isempty(refChan)
    rc = double(refChan(1));
    allowRef = true;
    % Use resolved enabled channels state instead of depending on a global.
    if ~isempty(st.enabledChans) && isempty(find(st.enabledChans == rc, 1))
        allowRef = false;
    end
    if allowRef && rc >= 1 && isempty(find(readChans == rc, 1))
        readChans = [readChans rc];
    end
end

phys = readChans;
if ~isempty(NI_SIGLAB_CHAN_MAP)
    m = double(NI_SIGLAB_CHAN_MAP(:)');
    for q = 1:numel(readChans)
        idx = round(readChans(q));
        if idx >= 1 && idx <= numel(m) && isfinite(m(idx))
            phys(q) = m(idx);
        end
    end
end

nread = numel(readChans);
if numel(phys) ~= nread
    phys = readChans;
end
[~, sortIdx] = sort(phys, 'ascend');
readChansSorted = readChans(sortIdx);

configuredRangeRead = nan(1,nread);
targetCouplingRead = cell(1,nread);
for ks = 1:nread
    k = sortIdx(ks);
    csel = max(1, min(numel(st.range), round(readChansSorted(ks))));
    rg = st.range(csel);
    if ~isfinite(rg) && csel <= numel(inpg)
        rg = inpg(csel);
    end
    [rg2, ofs2, md] = infer_mc_channel_params(csel);
    if ~isfinite(rg) && isfinite(rg2) && rg2 > 0
        rg = rg2;
    end
    if isfinite(ofs2)
        st.offset(csel) = ofs2;
    end
    if ~isempty(NI_SIGLAB_FORCE_RANGE)
        rg = abs(double(NI_SIGLAB_FORCE_RANGE));
    end
    if isfinite(rg) && rg > 0
        configuredRangeRead(k) = rg;
    end

    targetCpl = "AC";
    if ~isempty(NI_SIGLAB_FORCE_COUPLING)
        targetCpl = upper(string(NI_SIGLAB_FORCE_COUPLING));
    elseif strcmpi(measType, "IEPE")
        targetCpl = "AC";
    else
        if md == 1
            targetCpl = "DC";
        else
            targetCpl = "AC";
        end
    end
    targetCouplingRead{k} = char(targetCpl);
end

iepeStr = 'NaN';
if ~isempty(iepeI)
    iepeStr = sprintf('%.9g', iepeI);
end
cfgSig = [lower(st.device), ';', num2str(round(double(st.rate))), ';', ...
          char(measType), ';', iepeStr, ';', mat2str(readChans), ';', ...
          mat2str(phys), ';', mat2str(configuredRangeRead), ';', ...
          strjoin(targetCouplingRead, '|')];

needBuild = true;
if NI_SIGLAB_RUNTIME_REUSE && isfield(st.runtime, 'sig') && strcmp(st.runtime.sig, cfgSig) && ...
        isfield(st.runtime, 'dqobj') && ni_obj_valid(st.runtime.dqobj)
    needBuild = false;
end

if needBuild
    st = ni_runtime_reset(st, true);
    dqobj = daq("ni");
    dqobj.Rate = max(1, round(double(st.rate)));
    appliedRangeRead = nan(nread,2);
    appliedCouplingRead = cell(1,nread);
    [physSorted, sortIdx] = sort(phys, 'ascend');
    for ks = 1:nread
        k = sortIdx(ks);
        aiIdx = physSorted(ks)-1;
        chObj = addinput(dqobj, st.device, sprintf('ai%d', aiIdx), measType);
        if strcmpi(measType, "IEPE") && isprop(chObj, 'ExcitationCurrent') && ~isempty(iepeI)
            try
                chObj.ExcitationCurrent = iepeI;
            catch
            end
        end
        rg = configuredRangeRead(k);
        if isfinite(rg) && rg > 0 && isprop(chObj, 'Range')
            try
                chObj.Range = [-abs(rg), abs(rg)];
            catch
            end
        end
        if isprop(chObj, 'Range')
            try
                rr = double(chObj.Range);
                if numel(rr) >= 2
                    appliedRangeRead(k,:) = rr(1:2);
                end
            catch
            end
        end
        if isprop(chObj, 'Coupling')
            try
                chObj.Coupling = string(targetCouplingRead{k});
            catch
            end
            try
                appliedCouplingRead{k} = char(string(chObj.Coupling));
            catch
            end
        end
    end
    st.runtime.sig = cfgSig;
    st.runtime.dqobj = dqobj;
    st.runtime.readChans = readChans;
    st.runtime.sortIdx = sortIdx;
    st.runtime.phys = phys;
    st.runtime.configuredRangeRead = configuredRangeRead;
    st.runtime.appliedRangeRead = appliedRangeRead;
    st.runtime.appliedCouplingRead = appliedCouplingRead;
    st.runtime.measurementType = char(measType);
    if ~isempty(iepeI)
        st.runtime.iepeCurrent = iepeI;
    else
        st.runtime.iepeCurrent = NaN;
    end
else
    dqobj = st.runtime.dqobj;
    readChans = st.runtime.readChans;
    sortIdx = st.runtime.sortIdx;
    phys = st.runtime.phys;
    configuredRangeRead = st.runtime.configuredRangeRead;
    measType = string(st.runtime.measurementType);
    iepeI = st.runtime.iepeCurrent;
end

if isempty(sortIdx)
    [~, sortIdx] = sort(phys, 'ascend');
end

nchReq = numel(reqChans);
applyInfo = struct('logicalChans', reqChans, ...
                   'physicalAi', nan(1,nchReq), ...
                   'measurementType', char(measType), ...
                   'iepeCurrent', NaN, ...
                   'configuredRange', nan(1,nchReq), ...
                   'appliedRange', nan(nchReq,2), ...
                   'appliedCoupling', {cell(1,nchReq)}, ...
                   'timestamp', char(datetime('now', 'Format', 'yyyy-MM-dd HH:mm:ss')));
if isfinite(iepeI)
    applyInfo.iepeCurrent = iepeI;
end
for kr = 1:nchReq
    j = find(readChans == reqChans(kr), 1, 'first');
    if ~isempty(j)
        applyInfo.physicalAi(kr) = phys(j)-1;
        if j <= numel(configuredRangeRead)
            applyInfo.configuredRange(kr) = configuredRangeRead(j);
        end
        if isfield(st.runtime, 'appliedRangeRead') && size(st.runtime.appliedRangeRead,1) >= j
            applyInfo.appliedRange(kr,:) = st.runtime.appliedRangeRead(j,:);
        end
        if isfield(st.runtime, 'appliedCouplingRead') && numel(st.runtime.appliedCouplingRead) >= j
            applyInfo.appliedCoupling{kr} = st.runtime.appliedCouplingRead{j};
        end
    end
end
end

function [rg, ofs, md] = infer_mc_channel_params(ch)
% Read range/offset from legacy MC setup globals when InpGain callback is skipped.
rg = NaN;
ofs = NaN;
md = NaN;
try
    global VDLG1_S1
    if isempty(VDLG1_S1), return; end
    if size(VDLG1_S1,1) < ch, return; end
    if size(VDLG1_S1,2) >= 3
        m = VDLG1_S1(ch,3);
        if isfinite(m)
            md = m;
        end
    end
    if size(VDLG1_S1,2) >= 1
        vsel = VDLG1_S1(ch,1);
        try
            vr = chanvstr('volts',10,vsel);
            if isfinite(vr) && vr > 0
                rg = vr;
            end
        catch
        end
    end
    if size(VDLG1_S1,2) >= 6
        o = VDLG1_S1(ch,6);
        if isfinite(o)
            ofs = o;
        end
    end
catch
end
end

function out = adapt_kind(x, kind, refSig, outLen)
if nargin < 3
    refSig = [];
end
if nargin < 4 || isempty(outLen)
    outLen = size(x,1);
end
outLen = max(1, round(double(outLen)));
nraw = size(x,1);
nch = size(x,2);
if nraw < 1
    out = zeros(outLen, nch);
    return;
end

switch kind
    case {'timea','timei',''}
        out = resize_to_len(x, outLen);
    case {'aspeca','aspeci'}
        nfft = nraw;
        xw = apply_hann(x);
        X = fft(xw, nfft);
        pyy = (abs(X).^2) / max(1, nfft^2);
        out = pick_freq_bins(pyy, outLen);
    case 'fft'
        nfft = nraw;
        xw = apply_hann(x);
        X = fft(xw, nfft) / max(1, nfft);
        out = pick_freq_bins(X, outLen);
    case {'xfer','coh','cspec'}
        nfft = nraw;
        y = apply_hann(x);
        Y = fft(y, nfft);
        if isempty(refSig)
            if nch >= 1
                r = y(:,1);
            else
                r = zeros(nraw,1);
            end
        else
            r = apply_hann(refSig(:));
            if numel(r) < nraw
                r(end+1:nraw,1) = 0;
            elseif numel(r) > nraw
                r = r(1:nraw,1);
            end
        end
        R = fft(r, nfft);
        Srr = (R .* conj(R));
        epsv = 1e-12 + 0*Srr;
        switch kind
            case 'xfer'
                Syr = Y .* conj(R);
                H = Syr ./ (Srr + epsv);
                out = pick_freq_bins(H, outLen);
            case 'cspec'
                Syr = Y .* conj(R) / max(1, nfft);
                out = pick_freq_bins(Syr, outLen);
            case 'coh'
                Syr = Y .* conj(R);
                Syy = Y .* conj(Y);
                coh = (abs(Syr).^2) ./ (Syy .* Srr + epsv);
                coh = max(0, min(1, real(coh)));
                out = pick_freq_bins(coh, outLen);
        end
    case 'acor'
        nfft = nraw;
        xw = apply_hann(x);
        Rxx = xw;
        Rxx = ifft(fft(xw,nfft).*conj(fft(xw,nfft)), nfft);
        out = resize_to_len(real(Rxx), outLen);
    case 'ccor'
        nfft = nraw;
        y = apply_hann(x);
        Y = fft(y, nfft);
        if isempty(refSig)
            if nch >= 1
                r = y(:,1);
            else
                r = zeros(nraw,1);
            end
        else
            r = apply_hann(refSig(:));
            if numel(r) < nraw
                r(end+1:nraw,1) = 0;
            elseif numel(r) > nraw
                r = r(1:nraw,1);
            end
        end
        R = fft(r, nfft);
        Syr = Y .* conj(R);
        ccor = ifft(Syr, nfft);
        out = resize_to_len(real(ccor), outLen);
    case 'impulse'
        nfft = nraw;
        y = apply_hann(x);
        Y = fft(y, nfft);
        if isempty(refSig)
            if nch >= 1
                r = y(:,1);
            else
                r = zeros(nraw,1);
            end
        else
            r = apply_hann(refSig(:));
            if numel(r) < nraw
                r(end+1:nraw,1) = 0;
            elseif numel(r) > nraw
                r = r(1:nraw,1);
            end
        end
        R = fft(r, nfft);
        Srr = (R .* conj(R));
        epsv = 1e-12 + 0*Srr;
        Syr = Y .* conj(R);
        H = Syr ./ (Srr + epsv);
        imp = ifft(H, nfft);
        out = resize_to_len(real(imp), outLen);
    otherwise
        out = resize_to_len(x, outLen);
end
end

function y = resize_to_len(x, outLen)
y = x;
if size(y,1) < outLen
    y(end+1:outLen,:) = 0;
elseif size(y,1) > outLen
    y = y(1:outLen,:);
end
end

function y = pick_freq_bins(X, outLen)
n = size(X,1);
nkeep = min(outLen, n);
y = X(1:nkeep,:);
if nkeep < outLen
    y(end+1:outLen,:) = 0;
end
end

function y = apply_hann(x)
n = size(x,1);
if n <= 1
    y = x;
    return;
end
w = 0.5 - 0.5*cos(2*pi*(0:n-1)'/max(1,n-1));
y = x .* repmat(w, 1, size(x,2));
end

function [ok, out] = try_real_backend(action, args, nout)
ok = false;
out = cell(1, nout);
selfFile = mfilename('fullpath');
selfDir = fileparts(selfFile);
pathWithGuards = [pathsep, path, pathsep];
wasOnPath = contains(pathWithGuards, [pathsep, selfDir, pathsep]);
if wasOnPath
    rmpath(selfDir);
end
c = onCleanup(@()restore_self(selfDir, wasOnPath)); %#ok<NASGU>
rehash;
backend = which('siglab');
if isempty(backend) || strcmpi(backend, selfFile)
    return;
end
try
    [out{1:nout}] = feval('siglab', action, args{:});
    ok = true;
catch
    ok = false;
end
end

function restore_self(selfDir, wasOnPath)
if wasOnPath
    addpath(selfDir, '-begin');
end
end

function req = find_req(db, id)
req = [];
if isempty(db), return; end
ids = [db.id];
idx = find(ids == double(id), 1, 'first');
if isempty(idx), return; end
req = db(idx);
req.idx = idx;
end
