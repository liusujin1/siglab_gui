  function out=put_str(row,S,insert)
% function out=put_str(row,S,insert)
% put string "insert" into row "row" of matrix S
% Dick Benson DSP Technology
  [r,c] = size(S);         % rows and columns of input matrix
  if r~=0 | c~=0
     n = length(insert);      % # of characters in row to insert
     out = setstr(' ' + zeros(max(row,r),max(c,n))); % init output matrix
     out(1:r,1:c) = S;        % copy input matrix to output
     % add check for n=0 8/1/97 rab
     if n>0
        out(row,1:n) = insert;   % add new row
     else
        out = S;
     end;   
  else
     out=insert;   
  end;
%end function  

