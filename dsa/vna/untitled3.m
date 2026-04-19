[status,cmdout] = system('wmic CSproduct get UUID');
if ~status
    p = mfilename('fullpath');
    idx = strfind(p,'\');
    fid = fopen(fullfile(p(1:idx(end)-1),'windat.dat'),'r');
    if fid==-1
        return
    end
    data = fread(fid,inf,'char');
    uuid = char(data');
    fclose(fid);
    if isempty(strfind(cmdout,uuid))
        return
    else
        disp('pass')
    end
else
    return
end