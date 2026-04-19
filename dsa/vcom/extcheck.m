function fout = extcheck(ext,fname)
% function fout = extcheck(ext,fname)
%
% Adds extention ext to fname if fname
% does not already have an extension

fout = fname;
if isempty(findstr('.',fout)) fout = [fout '.' ext]; end;
